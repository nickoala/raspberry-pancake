import os
import sys
import argparse
import time
import socket
import json
import subprocess
import threading
import queue
import itertools
import urllib3
import logging
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime, timedelta
from functools import partial

def make_thread(fn, *args, **kwargs):
    return threading.Thread(target=fn, args=args, kwargs=kwargs)

def group_by_day(mp4_paths):
    def day_component(mp4_path):
        try:
            return datetime.strptime(
                os.path.basename(mp4_path), '%Y%m%d-%H%M.mp4').date()
        except ValueError:
            pass

        try:
            return datetime.strptime(
                os.path.basename(mp4_path), '%Y%m%d-%H%M%S.mp4').date()
        except ValueError:
            pass

        # Non-conforming filenames, to be filtered out.
        return None

    return [(k, list(g))
        for k,g
            in itertools.groupby(sorted(mp4_paths), day_component)
                if k is not None]

def rsync(mp4_paths,
          remote_host,
          port,
          remote_dir,
          logger):
    for day, paths in group_by_day(mp4_paths):
        # Remote directories are organized by yyyy/mm/dd
        dest = os.path.join(remote_dir,
                            day.strftime('%Y'),
                            day.strftime('%m'),
                            day.strftime('%d'))

        action = '{} -> {}:{}'.format(paths, remote_host, dest)

        cmd = ['rsync',
               '-avh',
               '-e', 'ssh -p {}'.format(port),
               '--rsync-path', 'mkdir -p {} && rsync'.format(dest)] \
             + paths \
             + ['{}:{}'.format(remote_host, dest)]

        try:
            logger.info('rsync start: {}'.format(action))

            p = subprocess.run(cmd,
                               text=True,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT)

            p.check_returncode()

        except subprocess.CalledProcessError as e:
            logger.warning('rsync error {}: {}\n{}'.format(
                                e.returncode, action, p.stdout))
        else:
            logger.info('rsync done: {}'.format(action))

def ftp(mp4_paths,
        remote_host,
        port,
        ftp_user,
        ftp_password,
        remote_dir,
        logger):
    for day, paths in group_by_day(mp4_paths):
        # Remote directories are organized by yyyy/mm/dd
        dest = os.path.join(remote_dir,
                            day.strftime('%Y'),
                            day.strftime('%m'),
                            day.strftime('%d'))

        action = '{} -> {}:{}'.format(paths, remote_host, dest)

        cmd = ['ncftpput',
               '-m', # make remote directory
               '-V', # no progress meter
               '-P', str(port),
               '-u', ftp_user,
               '-p', ftp_password,
               remote_host, dest] + paths

        try:
            logger.info('ftp start: {}'.format(action))

            p = subprocess.run(cmd,
                               text=True,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT)

            p.check_returncode()

        except subprocess.CalledProcessError as e:
            logger.warning('ftp error {}: {}\n{}'.format(
                                e.returncode, action, p.stdout))
        else:
            logger.info('ftp done: {}'.format(action))

def upload(mp4_count,
           minutes,
           upload_queue,
           rsync_dests,
           ftp_dests,
           logger):

    def do_upload_blocking(paths):
        logger.info('files to upload: {}'.format(paths))

        rsync_threads = \
            [make_thread(rsync, paths, *dest, logger)
                for dest in rsync_dests]

        ftp_threads = \
            [make_thread(ftp, paths, *dest, logger)
                for dest in ftp_dests]

        threads = rsync_threads + ftp_threads

        for t in threads:
            t.start()

        # Block until all threads are done.
        # Queue will not be read during this time.
        # This also ensures only one thread is transferring to
        # each destinations.
        for t in threads:
            t.join()

    # Set up time triggers
    if minutes:
        minutes = sorted(minutes)
        now = datetime.now()

        def normalize_hour(dt):
            return dt.replace(minute=0, second=0, microsecond=0)

        def repeat_hours(start_hour, n):
            hr = normalize_hour(start_hour)
            while 1:
                for _ in range(0, n):
                    yield hr
                hr = hr + timedelta(hours=1)

        def combine(hr, min):
            return hr.replace(minute=min)

        time_triggers = itertools.dropwhile(lambda dt: dt < now,
                            itertools.starmap(combine,
                                zip(repeat_hours(now, len(minutes)),
                                    itertools.cycle(minutes))))

        trigger = next(time_triggers)
    else:
        trigger = None

    mp4_paths = []
    while 1:
        try:
            # Clear queue. Move all items to list.
            while 1:
                path = upload_queue.get(timeout=1.1)
                mp4_paths.append(path)
        except queue.Empty:
            pass
        finally:
            now = datetime.now()
            if trigger and now >= trigger:
                logger.info('time trigger: {}'.format(
                            trigger.strftime('%Y-%m-%d %H:%M')))

                trigger = next(time_triggers)

                # If the previous upload took a long time, many triggers may
                # have been passed. Skip those triggers.
                while now >= trigger:
                    logger.info('skip trigger: {}'.format(
                                trigger.strftime('%Y-%m-%d %H:%M')))
                    trigger = next(time_triggers)

                if mp4_paths:
                    do_upload_blocking(mp4_paths)
                    mp4_paths = []
                else:
                    logger.info('nothing to upload')

            elif mp4_count and len(mp4_paths) >= mp4_count:
                logger.info('count trigger: {}'.format(len(mp4_paths)))

                do_upload_blocking(mp4_paths)
                mp4_paths = []

def telegram_bot_notify(detection,
                        mp4_path,
                        pool_manager,
                        token,
                        chat_id,
                        logger):

    action = '(detection={}, {}) -> {}'.format(detection, mp4_path, chat_id)

    def url(method):
        return 'https://api.telegram.org/bot{}/{}'.format(token, method)

    def send_message(text):
        logger.info('sendMessage start: {}'.format(action))

        r = pool_manager.request(
                    'POST',
                    url('sendMessage'),
                    fields=dict(chat_id=chat_id, text=text))

        return json.loads(r.data.decode('utf-8'))

    def send_video(path, caption):
        with open(path, 'rb') as f:
            logger.info('sendVideo start: {}'.format(action))

            r = pool_manager.request(
                        'POST',
                        url('sendVideo'),
                        fields=dict(
                                chat_id=chat_id,
                                video=(os.path.basename(path), f.read()),
                                caption=caption))

        return json.loads(r.data.decode('utf-8'))

    def make_text(d, *sentences):
        return '. '.join(
            ('Detection changed to {}'.format(d),)
            + sentences)

    try:
        if mp4_path is None:
            send_message(make_text(detection))

        elif mp4_path is False:
            send_message(make_text(detection, 'MP4 conversion failed.'))

        else:
            send_video(mp4_path, caption=make_text(detection))

    except Exception as e:
        logger.warning('send error: {}: {}'.format(e, action))

    else:
        logger.info('send done: {}'.format(action))

def notify(notify_queue, telegrams, logger):
    pool_manager = urllib3.PoolManager()
    while 1:
        detection, mp4_path = notify_queue.get()

        threads = \
            [make_thread(telegram_bot_notify,
                         detection,
                         mp4_path,
                         pool_manager,
                         *dest,
                         logger)
                for dest in telegrams]

        for t in threads:
            t.start()

        # Block until all threads are done.
        # Queue will not be read during this time.
        # This also ensures only one thread is sending to each account.
        for t in threads:
            t.join()

def MP4Box(h264_path, mp4_path, fps, logger):
    action = '{} <- {}'.format(mp4_path, h264_path)

    cmd = ['MP4Box', '-quiet',
           '-fps', str(fps),
           '-cat', h264_path,
           '-new', mp4_path]

    try:
        p = subprocess.run(cmd,
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT)

        p.check_returncode()

    except subprocess.CalledProcessError as e:
        logger.warning('MP4Box error {}: {}\n{}'.format(
                                e.returncode, action, p.stdout))
        return False

    else:
        logger.info('MP4Box done: {}'.format(action))
        return True

def process_detection_with_h264(detection,
                                h264,
                                mp4_dir,
                                fps,
                                notify_queue_put,
                                upload_queue_put,
                                logger):
    mp4_filename = os.path.splitext(os.path.basename(h264))[0] + '.mp4'
    mp4 = os.path.join(mp4_dir, mp4_filename)

    if MP4Box(h264, mp4, fps, logger):
        notify_queue_put((detection, mp4))
        upload_queue_put(mp4)
    else:
        notify_queue_put((detection, False))

def process_h264(h264, mp4_dir, fps, upload_queue_put, logger):
    mp4_filename = os.path.splitext(os.path.basename(h264))[0] + '.mp4'
    mp4 = os.path.join(mp4_dir, mp4_filename)

    if MP4Box(h264, mp4, fps, logger):
        upload_queue_put(mp4)

def log_queue_full(put_nowait, logger):
    def g(item):
        try:
            put_nowait(item)
        except queue.Full:
            logger.warning('queue full, item dropped: {}'.format(item))
        else:
            logger.info('item is queued: {}'.format(item))
    return g

def main():
    def raise_error_if_not_directory(action, path):
        if not os.path.exists(path):
            raise argparse.ArgumentError(action,
                    'directory not exist: {}'.format(path))

        if not os.path.isdir(path):
            raise argparse.ArgumentError(action,
                    'not a directory: {}'.format(path))

    class StoreConvertArgs(argparse.Action):
        def __call__(self, parser, namespace, values, option_string=None):
            dir, fps = values

            try:
                fps = int(fps)
            except ValueError:
                raise argparse.ArgumentError(self,
                        'Not an integer: {}'.format(fps))

            raise_error_if_not_directory(self, dir)
            setattr(namespace, self.dest, [dir, fps])

    class AppendRsyncArgs(argparse.Action):
        def __call__(self, parser, namespace, values, option_string=None):
            host, port, dir = values

            try:
                port = int(port)
            except ValueError:
                raise argparse.ArgumentError(self,
                        'Not an integer: {}'.format(port))

            values = [host, port, dir]

            if getattr(namespace, self.dest) is None:
                setattr(namespace, self.dest, [values])
            else:
                getattr(namespace, self.dest).append(values)

    class AppendFtpArgs(argparse.Action):
        def __call__(self, parser, namespace, values, option_string=None):
            host, port, user, password, dir = values

            try:
                port = int(port)
            except ValueError:
                raise argparse.ArgumentError(self,
                        'Not an integer: {}'.format(port))

            values = [host, port, user, password, dir]

            if getattr(namespace, self.dest) is None:
                setattr(namespace, self.dest, [values])
            else:
                getattr(namespace, self.dest).append(values)

    class StoreMinutesArgs(argparse.Action):
        def __call__(self, parser, namespace, values, option_string=None):
            if not all(map(lambda x: 0 <= x <= 59, values)):
                raise argparse.ArgumentError(self, 'Minute must be in 0..59')

            if len(values) != len(set(values)):
                raise argparse.ArgumentError(self, 'Some minutes are repeated')

            setattr(namespace, self.dest, sorted(values))

    parser = argparse.ArgumentParser()
    parser.add_argument('socket_name',
                        help='address of unix domain socket, in abstract namespace')
    parser.add_argument('--convert',
                        nargs=2,
                        action=StoreConvertArgs,
                        required=True,
                        metavar=('mp4_dir', 'fps'),
                        help='convert h264 files into mp4_dir'
                             ', at framerate fps'
                             ', require MP4Box')
    parser.add_argument('--upload-after-convert',
                        type=int,
                        metavar='N',
                        help='trigger upload after N conversions')
    parser.add_argument('--upload-minutes',
                        nargs='+',
                        type=int,
                        action=StoreMinutesArgs,
                        metavar='{0..59}',
                        help='trigger upload at minutes')
    parser.add_argument('--upload-rsync',
                        nargs=3,
                        action=AppendRsyncArgs,
                        default=[],
                        metavar=('user@host', 'port', 'remote_dir'),
                        help='better set up SSH key-based authentication')
    parser.add_argument('--upload-ftp',
                        nargs=5,
                        action=AppendFtpArgs,
                        default=[],
                        metavar=('host', 'port', 'user', 'password', 'remote_dir'),
                        help='require ncftpput')
    parser.add_argument('--upload-queue-size',
                        type=int,
                        default=8,
                        metavar='8')
    parser.add_argument('--notify-telegram',
                        nargs=2,
                        action='append',
                        default=[],
                        metavar=('bot_token', 'chat_id'),
                        help='use Telegram Bot API to notify chat_id'
                             ' of detection status')
    parser.add_argument('--notify-queue-size',
                        type=int,
                        default=8,
                        metavar='8')
    parser.add_argument('--log',
                        metavar='path')
    args = parser.parse_args()


    def has_upload_destinations(args):
        return (args.upload_rsync or args.upload_ftp)

    def has_upload_triggers(args):
        return (args.upload_after_convert or args.upload_minutes)

    if has_upload_destinations(args) and not has_upload_triggers(args):
        print('Please set upload trigger: --upload-after-convert or --upload-minutes')
        exit(3)

    if has_upload_triggers(args) and not has_upload_destinations(args):
        print('Please set upload destination: --upload-rsync or --upload-ftp')
        exit(3)


    # Use abstract namespace
    sock_address = '\0' + args.socket_name

    # Bind socket immediately. If it fails, nothing else will be done.
    main_sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    main_sock.bind(sock_address)


    if args.log:
        h = TimedRotatingFileHandler(args.log, when='midnight', backupCount=33)
        log_output = { 'handlers': [h] }
    else:
        log_output = { 'stream': sys.stdout }

    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s [%(name)s] %(message)s',
                        datefmt='%Y-%m-%d %H:%M:%S',
                        **log_output)

    main_logger = logging.getLogger('main')
    convert_logger = logging.getLogger('convert')


    if has_upload_destinations(args):
        upload_logger = logging.getLogger('upload')

        upload_queue = queue.Queue(args.upload_queue_size)

        # Wrap put_nowait() to log whether queue is full.
        upload_queue_put = \
                log_queue_full(upload_queue.put_nowait, upload_logger)

        upload_thread = make_thread(upload,
                                    args.upload_after_convert,
                                    args.upload_minutes,
                                    upload_queue,
                                    args.upload_rsync,
                                    args.upload_ftp,
                                    upload_logger)
        upload_thread.start()
    else:
        upload_queue_put = lambda item: None


    if args.notify_telegram:
        notify_logger = logging.getLogger('notify')

        notify_queue = queue.Queue(args.notify_queue_size)

        # Wrap put_nowait() to log whether queue is full.
        notify_queue_put = \
                log_queue_full(notify_queue.put_nowait, notify_logger)

        notify_thread = make_thread(notify,
                                    notify_queue,
                                    args.notify_telegram,
                                    notify_logger)
        notify_thread.start()
    else:
        notify_queue_put = lambda item: None


    ps_detection_with_h264 = partial(process_detection_with_h264,
                                     mp4_dir=args.convert[0],
                                     fps=args.convert[1],
                                     notify_queue_put=notify_queue_put,
                                     upload_queue_put=upload_queue_put,
                                     logger=convert_logger)

    ps_h264 = partial(process_h264,
                      mp4_dir=args.convert[0],
                      fps=args.convert[1],
                      upload_queue_put=upload_queue_put,
                      logger=convert_logger)

    while 1:
        try:
            data, _ = main_sock.recvfrom(512)
            data = data.decode()

            main_logger.info('received: {}'.format(data))

            obj = json.loads(data)

            if 'h264' in obj and 'detection' in obj:
                make_thread(ps_detection_with_h264, **obj).start()

            elif 'h264' in obj:
                make_thread(ps_h264, **obj).start()

            elif 'detection' in obj:
                notify_queue_put((obj['detection'], None))

            else:
                main_logger.warning('unknown object format: {}'.format(obj))

        except UnicodeError:
            main_logger.warning('UnicodeError: {}'.format(data))

        except json.JSONDecodeError:
            main_logger.warning('JSONDecodeError: {}'.format(data))


if __name__ == '__main__':
    main()
