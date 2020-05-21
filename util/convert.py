import sys
import os
import glob
import argparse
import itertools
import subprocess
import threading
from datetime import datetime

def convert_mp4(h264_dir, mp4_dir, fps, log):
    h264_paths = glob.iglob(os.path.join(h264_dir, '*.h264'))

    # Sort from oldest to newest
    h264_paths = sorted(h264_paths,
                        key=lambda path: os.stat(path).st_mtime)

    converted_mp4 = []

    # Convert every h264 except the newest, which is being written to.
    for h264_path in h264_paths[:-1]:
        mp4_filename = os.path.splitext(os.path.basename(h264_path))[0] + '.mp4'

        mp4_path = os.path.join(mp4_dir, mp4_filename)

        # Do convert if mp4 does not exist
        if not os.path.exists(mp4_path):
            cmd = ['MP4Box', '-quiet',
                   '-fps', str(fps),
                   '-cat', h264_path,
                   '-new', mp4_path]

            if log is None:
                # No stdout redirect. Let user see outputs.
                subprocess.run(cmd)

                converted_mp4.append(mp4_path)
            else:
                try:
                    p = subprocess.run(cmd,
                                    text=True,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT)

                    p.check_returncode()

                except FileNotFoundError:
                    log('MP4Box is not installed. No conversion happens.')
                    return []

                except subprocess.CalledProcessError as e:
                    log('Error {}: {} <- {}'.format(
                            e.returncode, mp4_path, h264_path))
                    log(p.stdout)
                    log('====================')

                else:
                    converted_mp4.append(mp4_path)
                    log('{} <- {}'.format(mp4_path, h264_path))

    return converted_mp4

def rsync(mp4_paths, mp4_dir, remote_host, port, remote_dir, log):
    # Normalize a datetime object for day comparison
    def normalize_day(dt):
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)

    def day_component(mp4_path):
        return normalize_day(
            datetime.strptime(
                os.path.basename(mp4_path), '%Y%m%d-%H%M.mp4'))

    days_touched = [day for day, _ in
            itertools.groupby(sorted(mp4_paths), day_component)]

    for day in days_touched:
        # Remote directories are organized by yyyy/mm/dd
        dest = os.path.join(remote_dir,
                            day.strftime('%Y'),
                            day.strftime('%m'),
                            day.strftime('%d'))

        # Use wildcard to select local files on the same day
        src = os.path.join(mp4_dir, day.strftime('%Y%m%d-*.mp4'))

        # On subprocess.run(), shell=True because we need wildcard expansion
        cmd = ('rsync -avh '
               '-e "ssh -p {port}" '
               '--rsync-path "mkdir -p {dest} && rsync" '
               '{src} {host}:{dest}').format(src=src,
                                             dest=dest,
                                             host=remote_host,
                                             port=port)

        if log is None:
            subprocess.run(cmd, shell=True)
        else:
            log('RSYNC Start: {} -> {}:{}'.format(src, remote_host, dest))
            try:
                p = subprocess.run(cmd,
                                shell=True,
                                text=True,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT)

                p.check_returncode()

            except subprocess.CalledProcessError as e:
                log('RSYNC Error {}: {} -> {}:{}'.format(
                        e.returncode, src, remote_host, dest))
                log(p.stdout)
                log('====================')
            else:
                log('RSYNC Done {}: {} -> {}:{}'.format(
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        src, remote_host, dest))

def main():
    parser = argparse.ArgumentParser(
        description='Convert h264 to mp4, optionally upload to remote server.')
    parser.add_argument('h264_dir',
                        help='directory from which h264 files are read')
    parser.add_argument('mp4_dir',
                        help='directory in which mp4 files are saved')
    parser.add_argument('fps', type=int,
                        help='mp4 conversion framerate')
    parser.add_argument('--rsync', nargs=3, action='append',
                        metavar=('host', 'port', 'dir'),
                        help='rsync to a remote directory')
    parser.add_argument('--log', metavar='path')
    args = parser.parse_args()

    if args.rsync:
        for x in args.rsync:
            try:
                x[1] = int(x[1])
            except ValueError:
                print('Port number must be integer:', x[1])
                exit(1)

    log_lock = threading.Lock()

    # Thread-safe logger
    def write_log(line):
        with log_lock:
            with open(args.log, 'a') as f:
                f.write(line)
                f.write('\n')
                f.flush()

    log = write_log if args.log else None

    if log:
        log(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

    mp4_paths = convert_mp4(args.h264_dir, args.mp4_dir, args.fps, log)

    threads = []

    if args.rsync:
        def make_rsync_thread(remote_host, port, remote_dir):
            return threading.Thread(target=rsync,
                                    args=(mp4_paths,
                                          args.mp4_dir,
                                          remote_host,
                                          port,
                                          remote_dir,
                                          log))

        threads = threads + [make_rsync_thread(*x) for x in args.rsync]

    # Add more threads here, if you support other methods of upload.

    for t in threads:
        t.start()

    # Wait for all threads to finish
    for t in threads:
        t.join()


if __name__ == '__main__':
    main()
