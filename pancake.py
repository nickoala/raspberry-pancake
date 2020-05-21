import argparse
import base64
import hashlib
import os
import sys
import time
import io
import signal
import random
import string
import threading
import itertools
import traceback
import socket
import json
from datetime import datetime
from functools import partial

import picamera

import tornado.web
import tornado.websocket
import tornado.gen
from tornado.ioloop import PeriodicCallback


class IndexHandler(tornado.web.RequestHandler):
    def initialize(self, check):
        self.check_is_ok = check

    def get(self):
        if not self.check_is_ok(self):
            self.redirect("/login")
        else:
            self.render("index.html")


class LoginHandler(tornado.web.RequestHandler):
    def initialize(self, check):
        self.check_is_ok = check

    def get(self):
        self.render("login.html")

    async def post(self):
        password = self.get_argument("i", "")

        if self.check_is_ok(password, self):
            self.redirect("/")
        else:
            await tornado.gen.sleep(3)
            self.redirect(u"/login?error")


class ErrorHandler(tornado.web.RequestHandler):
    def get(self):
        self.send_error(status_code=403)


class WebSocket(tornado.websocket.WebSocketHandler):
    def initialize(self, check, double_buffer, max_fps):
        self.check_is_ok = check
        self.frame = double_buffer
        self.min_frame_separation = 1. / max_fps
        self.last_frame_time = None

    def check_cache(self):
        if self.check_is_ok is True:
            return True
        elif self.check_is_ok is False:
            return False
        else:
            self.check_is_ok = self.check_is_ok(self)
            return self.check_is_ok

    async def on_message(self, message):
        if not self.check_cache():
            self.close()
            return

        if message == "read_frame":
            # Enforce max_fps. Ensure frames are not sent too fast.
            if self.last_frame_time is not None:
                elapsed = time.time() - self.last_frame_time
                delay = self.min_frame_separation - elapsed

                if delay > 0:
                    await tornado.gen.sleep(delay)

            # Usually this does not block. It is only to guard at the very
            # beginning when the first frame has not yet been captured.
            self.frame.wait_for_readable()

            self.write_message(self.frame.read(), binary=True)
            self.last_frame_time = time.time()
        else:
            self.close()


class DoubleBuffer(object):
    def __init__(self):
        self.lock = threading.Lock()
        self.pool = itertools.cycle([io.BytesIO(), io.BytesIO()])
        self.readable = threading.Event()
        self.fixed = None
        self.loose = None

    def wait_for_readable(self):
        self.readable.wait()

    def read(self):
        with self.lock:
            return self.fixed.getvalue()

    def switch(self):
        with self.lock:
            # Switch buffer
            self.fixed = self.loose

            # Note: switch() has to be called twice before `self.fixed` becomes
            # non-None. Only then can it be readable.
            if self.fixed:
                self.readable.set()
                self.fixed.truncate()
                self.fixed.seek(0)

            # Return next buffer for caller to write to.
            self.loose = next(self.pool)
            self.loose.truncate()
            self.loose.seek(0)
            return self.loose


def attach_splitter_port(port, *fs):
    return [partial(f, splitter_port=port) for f in fs]


def capture_jpg_for_streaming(camera, splitter_port, double_buffer):
    start_recording, \
    wait_recording,  \
    stop_recording = \
            attach_splitter_port(splitter_port,
                    camera.start_recording,
                    camera.wait_recording,
                    camera.stop_recording)

    class SplitFrames(object):
        def __init__(self):
            self.frame = None

        def write(self, buf):
            if buf.startswith(b'\xff\xd8'):
                self.frame = double_buffer.switch()
            self.frame.write(buf)

    start_recording(SplitFrames(), 'mjpeg')
    try:
        while 1:
            wait_recording(10)
    finally:
        stop_recording()


def record_continuously(camera,
                        splitter_port,
                        resize,
                        outdir,
                        emitter):
    record_sequence, \
    wait_recording,  \
    stop_recording = \
            attach_splitter_port(splitter_port,
                    camera.record_sequence,
                    camera.wait_recording,
                    camera.stop_recording)

    def datetime_filenames():
        while 1:
            yield (
                os.path.join(
                    outdir,
                    datetime.now().strftime('%Y%m%d-%H%M.h264')))

    def seconds_remaining_in_quarter():
        length = 15 * 60
        now = datetime.now()
        seconds = now.minute * 60 + now.second

        # Number of seconds remaining in this quarter.
        # This ensures recordings are split at 0, 15, 30, 45-minute mark.
        return length - seconds % length

    try:
        fname_ended = None
        for fname in record_sequence(datetime_filenames(), resize=resize):
            if fname_ended:
                emitter(h264=os.path.abspath(fname_ended))

            wait_recording(seconds_remaining_in_quarter())
            fname_ended = fname
    finally:
        stop_recording()


def record_detection_edge(camera,
                          splitter_port,
                          resize,
                          event,
                          outdir,
                          emitter):
    start_recording, \
    wait_recording,  \
    stop_recording,  \
    CircularIO =     \
            attach_splitter_port(splitter_port,
                    camera.start_recording,
                    camera.wait_recording,
                    camera.stop_recording,
                    picamera.PiCameraCircularIO)

    def datetime_filename():
        return os.path.join(
                    outdir,
                    datetime.now().strftime('%Y%m%d-%H%M%S.h264'))

    event_is_set = False
    circular = CircularIO(camera, seconds=6)
    start_recording(circular, 'h264', resize=resize)
    try:
        while 1:
            wait_recording(1)

            # See an edge ...
            if event_is_set != event.is_set():
                event_is_set = not event_is_set

                # Record for a few more seconds to surround the edge.
                fname = datetime_filename()
                wait_recording(3)
                circular.copy_to(fname, seconds=6)

                emitter(h264=os.path.abspath(fname), detection=event_is_set)
    finally:
        stop_recording()


# Mirror behaviour of record_detection_edges(), but no recording, only emit.
def monitor_detection(event, emitter):
    event_is_set = False
    while 1:
        time.sleep(1)
        if event_is_set != event.is_set():
            event_is_set = not event_is_set
            time.sleep(3)
            emitter(detection=event_is_set)


def record_detection_all(camera,
                         splitter_port,
                         resize,
                         event,
                         outdir,
                         max_seconds,
                         emitter):
    start_recording, \
    wait_recording,  \
    split_recording, \
    stop_recording,  \
    CircularIO =     \
            attach_splitter_port(splitter_port,
                    camera.start_recording,
                    camera.wait_recording,
                    camera.split_recording,
                    camera.stop_recording,
                    picamera.PiCameraCircularIO)

    def datetime_filename():
        return os.path.join(
                    outdir,
                    datetime.now().strftime('%Y%m%d-%H%M%S.h264'))

    file = None
    circular = CircularIO(camera, seconds=6)
    start_recording(circular, 'h264', resize=resize)
    try:
        while 1:
            while 1:
                wait_recording(1)

                # See a detection ...
                if event.is_set():
                    fname = datetime_filename()
                    wait_recording(3)

                    # This file contains the edge.
                    circular.copy_to(fname, seconds=6)
                    fname_ended = fname

                    # Next file records further detections.
                    fname = datetime_filename()
                    split_recording(fname)
                    split_second = time.time()
                    circular.clear()

                    emitter(h264=os.path.abspath(fname_ended), detection=True)
                    break

            while 1:
                wait_recording(1)

                # See no detection ...
                if not event.is_set():
                    # Record for a few more seconds to surround the edge.
                    wait_recording(3)

                    # If detection becomes true again, keep recording.

                    # If detection really is gone ...
                    if not event.is_set():
                        # Steer recording back to circular buffer.
                        circular.clear()
                        split_recording(circular)

                        fname_ended = fname
                        fname = None

                        emitter(detection=False)
                        emitter(h264=os.path.abspath(fname_ended))
                        break

                # Split recording if time is up.
                if time.time() - split_second > max_seconds:
                    fname_ended = fname
                    fname = datetime_filename()
                    split_recording(fname)
                    split_second = time.time()

                    emitter(h264=os.path.abspath(fname_ended))
    finally:
        stop_recording()


# Wrap a function to prevent errors from leaking out. This is especially
# important when dealing with external programs, such as custom detect function
# and socket emitter. In the case of socket emitter, uncaught errors can stop
# recording altogether. I don't want that to happen.
def contain_errors(f):
    def g(*a, **kw):
        try:
            f(*a, **kw)
        except Exception:
            traceback.print_exc(file=sys.stderr)
    return g


@contain_errors
def detect_noimage(event, detector, interval):
    try:
        while 1:
            if detector():
                event.set()
            else:
                event.clear()
            time.sleep(interval)
    finally:
        event.clear()


@contain_errors
def detect_image(double_buffer, event, detector, interval):
    try:
        double_buffer.wait_for_readable()
        while 1:
            if detector(double_buffer.read()):
                event.set()
            else:
                event.clear()
            time.sleep(interval)
    finally:
        event.clear()


def capture_rgb_for_detection(camera, splitter_port, resize, double_buffer):
    def g():
        while 1:
            yield double_buffer.switch()

    camera.capture_sequence(g(), 'rgb',
                            resize=resize,
                            use_video_port=True,
                            splitter_port=splitter_port)


def annotate(camera):
    camera.annotate_text = datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def configure_camera(resolution,
                     framerate,
                     vflip,
                     hflip,
                     annotate_text_size):
    c = picamera.PiCamera(
            resolution=resolution,
            framerate=framerate)
    c.vflip = vflip
    c.hflip = hflip
    c.annotate_text_size = annotate_text_size
    time.sleep(2)  # warm-up
    return c


def make_authenticator(password_hash, cookie_name):
    def is_authenticated(cookie_holder):
        return bool(cookie_holder.get_secure_cookie(cookie_name))

    def check_and_set(password, cookie_holder):
        if hashlib.sha512(password.encode()).hexdigest() == password_hash:
            cookie_holder.set_secure_cookie(cookie_name, str(time.time()))
            return True
        else:
            return False

    return is_authenticated, check_and_set


def random_string(k):
    return ''.join(random.choices(
                    string.digits +
                    string.ascii_lowercase +
                    string.ascii_uppercase,
                    k=k))


def main():
    def raise_error_if_not_directory(action, path):
        if not os.path.exists(path):
            raise argparse.ArgumentError(action,
                    'directory not exist: {}'.format(path))

        if not os.path.isdir(path):
            raise argparse.ArgumentError(action,
                    'not a directory: {}'.format(path))

    class StoreRecordContinuousArgs(argparse.Action):
        def __call__(self, parser, namespace, value, option_string=None):
            raise_error_if_not_directory(self, value)
            setattr(namespace, self.dest, value)

    class StoreRecordDetectionArgs(argparse.Action):
        def __call__(self, parser, namespace, values, option_string=None):
            mode, dir = values

            choices = ['edge', 'all']
            if mode not in choices:
                raise argparse.ArgumentError(self,
                        'invalid mode: {}. Choices {}'.format(mode, choices))

            raise_error_if_not_directory(self, dir)
            setattr(namespace, self.dest, values)

    class StoreLoginArgs(argparse.Action):
        def __call__(self, parser, namespace, value, option_string=None):
            # Read password hash from file
            hash = value.read().strip()
            setattr(namespace, self.dest, hash)

    parser = argparse.ArgumentParser()
    parser.add_argument('--port',
                        type=int,
                        default=8000,
                        metavar='8000')
    parser.add_argument('--resolution',
                        nargs=2,
                        type=int,
                        default=[320, 240],
                        metavar=('320', '240'))
    parser.add_argument('--fps',
                        type=int,
                        default=12,
                        metavar='12',
                        help='camera framerate')
    parser.add_argument('--vflip',
                        action='store_true')
    parser.add_argument('--hflip',
                        action='store_true')
    parser.add_argument('--annotate-text-size',
                        type=int,
                        default=24,
                        metavar='24')
    parser.add_argument('--max-stream-fps',
                        type=int,
                        default=10,
                        metavar='10',
                        help='max streaming framerate')
    parser.add_argument('--record-continuous',
                        action=StoreRecordContinuousArgs,
                        metavar='h264_dir',
                        help='record video into h264_dir, 15 minutes apiece')
    parser.add_argument('--record-detection',
                        nargs=2,
                        action=StoreRecordDetectionArgs,
                        metavar=('{edge,all}', 'h264_dir'),
                        help='on detection, record video into h264_dir')
    parser.add_argument('--record-resize',
                        nargs=2,
                        type=int,
                        metavar=('w', 'h'),
                        help='resize image for video recording')
    parser.add_argument('--detect',
                        choices=['no-image', 'image'],
                        help='detect() or detect(bytes)?')
    parser.add_argument('--detect-resize',
                        nargs=2,
                        type=int,
                        metavar=('w', 'h'),
                        help='resize image for detection')
    parser.add_argument('--detect-interval',
                        type=int,
                        metavar='seconds',
                        help='time between detect() calls')
    parser.add_argument('--emit',
                        metavar='socket_address',
                        help='unix domain socket to post-processor')
    parser.add_argument('--login',
                        type=argparse.FileType('r'),
                        action=StoreLoginArgs,
                        dest='login_hash',
                        metavar='password_hash_file',
                        help='password-protect streaming webpage')

    def validate_args(args):
        if args.record_resize and \
                not (args.record_continuous or args.record_detection):
            print('--record-resize is set.'
                  ' Please also set --record-continuous or --record-detection.')
            exit(3)

        if args.record_detection and not args.detect:
            print('--record-detection is set.'
                  ' Please also set --detect to generate detections.')
            exit(3)

        if args.detect_resize and not args.detect:
            print('--detect-resize is set. Please also set --detect.')
            exit(3)

        if args.detect_interval and not args.detect:
            print('--detect-interval is set. Please also set --detect.')
            exit(3)

    args = parser.parse_args()
    validate_args(args)

    if args.detect:
        from customize import detect as detect_function


    # Use datagram socket to emit to post-processor
    if args.emit:
        server_address = '\0' + args.emit  # abstract namespace

        datagram_socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        emitter_lock = threading.Lock()

        @contain_errors
        def emitter(**obj):
            with emitter_lock:
                datagram_socket.sendto(json.dumps(obj).encode(), server_address)
    else:
        def emitter(**obj):
            pass


    def make_info_string(head, **kw):
        tail = ', '.join([
                    '{} {}'.format(k,v) if v is not None
                        else 'no {}'.format(k)
                            for k,v in kw.items()])
        return '{}: {}'.format(head, tail)


    root = os.path.normpath(os.path.dirname(__file__))
    cookie_name = 'raspberry-pancake'

    if args.login_hash:
        # Functions to be used by handlers
        is_authenticated, check_and_set = \
                make_authenticator(args.login_hash, cookie_name)
    else:
        # No login required, acts as if authenticated, returns True regardless
        is_authenticated, check_and_set = \
                lambda *x: True, lambda *x: True


    print(make_info_string('Configure camera',
                           resolution=args.resolution,
                           fps=args.fps))

    camera = configure_camera(args.resolution,
                              args.fps,
                              args.vflip,
                              args.hflip,
                              args.annotate_text_size)

    double_buffer = DoubleBuffer()

    handlers = [(r"/", IndexHandler,
                    dict(check=is_authenticated)),

                (r"/login", LoginHandler,
                    dict(check=check_and_set)),

                (r"/websocket", WebSocket,
                    dict(check=is_authenticated,
                         double_buffer=double_buffer,
                         max_fps=args.max_stream_fps)),

                (r"/static/(.*)", tornado.web.StaticFileHandler,
                    dict(path=os.path.join(root, "static")))]

    application = tornado.web.Application(
                            handlers,
                            cookie_secret=random_string(60))


    # Splitter ports
    stream_port,            \
    record_continuous_port, \
    record_detection_port,  \
    detect_port             = 0, 1, 2, 3

    ioloop = tornado.ioloop.IOLoop.instance()

    print(make_info_string('Capture JPG for streaming',
                           splitter_port=stream_port))

    ioloop.run_in_executor(None, capture_jpg_for_streaming,
                           camera,
                           stream_port,
                           double_buffer)


    if args.record_continuous:
        print(make_info_string('Record continuously',
                               splitter_port=record_continuous_port,
                               resize=args.record_resize))

        ioloop.run_in_executor(None, record_continuously,
                               camera,
                               record_continuous_port,
                               args.record_resize,
                               args.record_continuous,
                               emitter)


    detection_event = threading.Event()

    if args.record_detection:
        mode, outdir = args.record_detection

        if mode == 'edge':
            print(make_info_string('Record detection edges',
                                   splitter_port=record_detection_port,
                                   resize=args.record_resize))

            ioloop.run_in_executor(None, record_detection_edge,
                                   camera,
                                   record_detection_port,
                                   args.record_resize,
                                   detection_event,
                                   outdir,
                                   emitter)
        elif mode == 'all':
            print(make_info_string('Record all detections',
                                   splitter_port=record_detection_port,
                                   resize=args.record_resize))

            ioloop.run_in_executor(None, record_detection_all,
                                   camera,
                                   record_detection_port,
                                   args.record_resize,
                                   detection_event,
                                   outdir,
                                   60 * 15,
                                   emitter)
        else:
            raise ValueError()

    elif args.detect:
        print('Monitor detection')
        ioloop.run_in_executor(None, monitor_detection,
                               detection_event,
                               emitter)


    if args.detect:
        detect_interval = args.detect_interval or (1. / args.fps)

        if args.detect == 'no-image':
            print('Perform detection without image')

                                      # log error produced by `detect_function`
            ioloop.run_in_executor(None, detect_noimage,
                                   detection_event,
                                   detect_function,
                                   detect_interval)
        elif args.detect == 'image':
            print(make_info_string('Perform image detection',
                                   splitter_port=detect_port,
                                   resize=args.detect_resize))

            detect_buffer = DoubleBuffer()

            ioloop.run_in_executor(None, capture_rgb_for_detection,
                                   camera,
                                   detect_port,
                                   args.detect_resize,
                                   detect_buffer)

                                      # log error produced by `detect_function`
            ioloop.run_in_executor(None, detect_image,
                                   detect_buffer,
                                   detection_event,
                                   detect_function,
                                   detect_interval)
        else:
            raise ValueError()


    # Refresh time on picture
    annotator = PeriodicCallback(partial(annotate, camera), 1000)
    annotator.start()

    def stop_server(signum, frame):
        annotator.stop()
        camera.close()
        ioloop.stop()
        exit(0)  # need this to terminate for some reason
        # may still need to Ctrl-C twice

    signal.signal(signal.SIGINT, stop_server)

    application.listen(args.port)
    print("Serving webcam on port {} ...".format(args.port))
    ioloop.start()


if __name__ == "__main__":
    main()
