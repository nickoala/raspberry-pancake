Raspberry Pancake
=================

There are no shortage of open source CCTV solutions claimed to work on Raspberry
Pi. Examples include [MotionEyeOS](https://github.com/ccrisan/motioneyeos),
[RPi-Cam-Web-Interface](https://elinux.org/RPi-Cam-Web-Interface), among others.
Many offer motion detection (which I don't need); some expose a lot of options
(which are confusing). None works reliably on a Raspberry Pi according to my
test (tweaking settings is a surefire way to break something). I am happy to
find a **simple and reliable** alternative in [Camp](https://github.com/patrickfuller/camp).

This project derives from [Patrick Fuller's Camp](https://github.com/patrickfuller/camp),
a simple web server hosting a Raspberry Pi camera. Its bare-bone architecture
allows me to add functions easily. All configurations are done via command line,
and some functions are exposed to be customizable programmatically.

Functions include:

- Web page to view the camera
- Record continuously, or record on detection, or both
- User-defined detection. You decide what _detection_ means.
- Backup recordings to remote servers
- Send detection notifications via Telegram

**I can personally attest to its reliability because I use it as CCTV in my
office.**

Contrary to Camp, this project does not support USB camera. I want to keep it
simple.

The name _Pancake_ was meant to be a mix of _Pan_, _Cam_, and _Record_. I
originally planned to incorporate pan-tilt functionality, but decided not to do
it in the end. The name had been born, so be it.


Installation
------------

Hardware tested: **Pi 3B+, Pi 3B, Pi Zero W**

Software platform: **Raspbian Buster Lite, Python 3**

I would begin by changing user password, hostname, timezone, and enabling camera.

```
sudo apt-get update
sudo apt-get dist-upgrade
sudo apt-get install python3-pip gpac ncftp
sudo pip3 install picamera tornado
```

`gpac` and `ncftp` are optional. I see no harm in including them.

`gpac` is for the command `MP4Box`, which converts h264 to mp4. `ncftp` is for
the command `ncftpput`, which sends a file via FTP. Both may (or may not) be
needed by the script `postproc.py`, which post-processes h264 files generated by
the recording program, `pancake.py`.

In addition to `pancake.py` and `postproc.py`, there are a few utility scripts
to clean up old recordings, to generate password hash, etc. They are introduced
in turn.


pancake.py
----------

This is a tornado webserver with added functions. It requires:

1. Template files: `index.html` and `login.html`

2. the `static/` directory

_Note that all recorded files are in h264._

_Option parameters are shown either as defaults or placeholders. I hope context
makes it clear._

```
$ python3 pancake.py --help
usage: pancake.py [-h] [--port 8000] [--resolution 320 240] [--fps 12]
                  [--vflip] [--hflip] [--annotate-text-size 24]
                  [--max-stream-fps 10] [--record-continuous h264_dir]
                  [--record-detection {edge,all} h264_dir]
                  [--record-resize w h] [--detect {no-image,image}]
                  [--detect-resize w h] [--detect-interval seconds]
                  [--emit socket_address] [--login password_hash_file]

optional arguments:
  -h, --help            show this help message and exit
  --port 8000           webserver port hosting the camera live view
  --resolution 320 240  resolution of camera live view
  --fps 12              camera framerate
  --vflip
  --hflip
  --annotate-text-size 24
  --max-stream-fps 10   max streaming framerate
  --record-continuous h264_dir
                        record video into h264_dir, 15 minutes apiece
  --record-detection {edge,all} h264_dir
                        on detection, record video into h264_dir
  --record-resize w h   resize image for video recording
  --detect {no-image,image}
                        detect() or detect(bytes)?
  --detect-resize w h   resize image for detection
  --detect-interval seconds
                        time between detect() calls
  --emit socket_address
                        unix domain socket to post-processor
  --login password_hash_file
                        password-protect streaming webpage
```

`--port 8000`  
`--resolution 320 240`  
`--fps 12`  
`--vflip`  
`--hflip`  
`--annotate-text-size 24`  

- Self-explanatory, I hope.
- Note that `--fps` affects recorded file sizes. The higher the framerate,
  the bigger the files.


`--max-stream-fps 10`

- The maximum number of frames sent to browser every second. It helps preserve
  bandwidth.
- The _actual_ number of frames sent to browser every second is restricted by
  another factor. The webserver does not send a new frame until it receives the
  browser's acknowledgement of the last sent frame. In other words, the
  webserver only sends as many frames as the network allows. This prevents
  congestion and makes sure the browser gets an up-to-date frame as soon as
  possible.
- No matter how fast the network, the _actual_ FPS to browser does not exceed
  `--max-stream-fps`.


`--record-continuous h264_dir`

- Record continuously. Clips are written to directory `h264_dir`.
- Recording is split at 00-, 15-, 30-, 45-minute mark. The first clip may be
  shorter than 15 minutes.
- Filename format is `YYYYMMDD-hhmm.h264`. E.g. the clip starting at
  2020-06-23 17:15 is named `20200623-1715.h264`


`--record-detection {edge,all} h264_dir`

- Record on detection. Clips are written to directory `h264_dir`.
- This option requires `--detect` to be given at the same time.
- If `edge`, record around _edges of detection_. An edge is detection changing
  from false to true, or from true to false. Clips are 4-6 seconds long each,
  centered around edges.
- If `all`, record for _as long as detection is true_. If detection remains true
  for a long time, recording is split every 15 minutes.
- Filename format is `YYYYMMDD-hhmmss.h264`. E.g. the clip starting at
  2020-06-23 18:05:11 is named `20200623-180511.h264`
- Since the filename format is different from that of `--record-continuous`,
  clips may be written to the same `h264_dir` with no danger.
- `--record-continuous` and `--record-detection` may be used at the same time.


`--record-resize w h`

- Recording resolution. If not specified, same as `--resolution`.


`--detect {no-image,image}`

- This option determines what your detect function looks like.
- If `no-image`, detect function is `detect()`. This means your detection is not
  visual (does not depend on a captured image).
- If `image`, detect function is `detect(bytes)`, `bytes` being the RGB values
  of a captured frame. This likely means you want to detect something visual.
- The detect function must return a boolean value, `True` or `False`. It must be
  defined in `customize.py`, supplied by you.
- The thread calling detect function is independent from the thread recording
  detections. A slow detect function does not interfere with recording.
  Don't worry.
- Supplying detect function does not necessarily means you have to `--record-detection`
  (although the latter requires the former). You don't have to record detections.
  You can allow the post-processor to handle detection edges. See the `--emit` option.


`--detect-resize w h`

- Resize captured images before passing to `detect(bytes)`. If not specified,
  image resolution follows `--resolution`.
- Useful if your detection/classification algorithm requires a fixed input size,
  like neural nets.


`--detect-interval seconds`

- Delay some time between calls to detect function. `seconds` can be smaller than 1.
- If not specified, default to 1/FPS, where FPS is given by `--fps`.


`--emit socket_address`

- Emit messages to a Unix domain socket, to be received by a script like
  `postproc.py`. A null character is prepended to `socket_address` to make a
  name in the abstract namespace. This name serves as the point of contact
  between `pancake.py` and the post-processor, e.g. `postproc.py`.
- Messages are emitted when:
  - an h264 file is finished, usually when recording is split or when detection
    turns to false.
  - a detection edge occurs (detection changing from false to true, or from
    true to false).
- What to do with the messages is up to the receiving end.
  E.g. `postproc.py` can convert h264 to mp4, then send them to a remote server
  for backup. It can also react to a detection edge by sending a Telegram message.


`--login password_hash_file`

- Password-protect the camera live view web page.
- `password_hash_file` is a path to a file holding a hash. Generate this file
  with the utility script `set_password.py`.


postproc.py
-----------

```
$ python3 postproc.py --help
usage: postproc.py [-h] --convert mp4_dir fps [--upload-after-convert N]
                   [--upload-minutes {0..59} [{0..59} ...]]
                   [--upload-scp user@host port remote_dir]
                   [--upload-ftp host port user password remote_dir]
                   [--upload-queue-size 8]
                   [--notify-telegram bot_token chat_id]
                   [--notify-queue-size 8] [--log path]
                   socket_name

positional arguments:
  socket_name           address of unix domain socket, in abstract namespace

optional arguments:
  -h, --help            show this help message and exit
  --convert mp4_dir fps
                        convert h264 files into mp4_dir, at framerate fps,
                        require MP4Box
  --upload-after-convert N
                        trigger upload after N conversions
  --upload-minutes {0..59} [{0..59} ...]
                        trigger upload at minutes
  --upload-scp user@host port remote_dir
                        better set up SSH key-based authentication
  --upload-ftp host port user password remote_dir
                        require ncftpput
  --upload-queue-size 8
  --notify-telegram bot_token chat_id
                        use Telegram Bot API to notify chat_id of detection
                        status
  --notify-queue-size 8
  --log path
```

`socket_name`

- Unix domain socket to receive messages. A null character is prepended to
  `socket_name` to make a name in the abstract namespace.


`--convert mp4_dir fps`

- On receiving a message indicating a finished h264 file, convert that file to
  mp4 with the given `fps`. The new mp4 file is written to directory `mp4_dir`,
  with the same name as the original h264 file, except the extension. For
  example, `20200624-0845.h264` will be converted to `20200624-0845.mp4`.
- Requires the command `MP4Box`


`--upload-after-convert N`

- Trigger upload after having converted `N` mp4 files.
- This option may be used in combination with `--upload-minutes`.
- Upload destinations are specified with `--upload-scp` and `--upload-ftp`.


`--upload-minutes {0..59} [{0..59} ...]`

- Trigger upload at certain minutes.
- This option may be used in combination with `--upload-after-convert`.
- Upload destinations are specified with `--upload-scp` and `--upload-ftp`.


`--upload-scp user@host port remote_dir`

- Use scp to upload mp4 files.
- Uploaded files are organized into a directory tree of `YYYY/MM/DD`. For
  example, the file `20200624-0900.mp4` is copied to the remote directory
  `user@host:remote_dir/2020/06/24`.
- Multiple `--upload-scp` is allowed. May also be used in combination with
  `--upload-ftp`.


`--upload-ftp host port user password remote_dir`

- Use ftp to upload mp4 files.
- Uploaded files are organized into a directory tree of `YYYY/MM/DD`. For
  example, the file `20200624-0900.mp4` is copied to the remote directory
  `remote_dir/2020/06/24`.
- Multiple `--upload-ftp` is allowed. May also be used in combination with
  `--upload-scp`.
- Requires the command `ncftpput`


`--upload-queue-size 8`

- Mp4 files are queued to wait for upload. When an upload is proceeding, the
  queue is not read. If an upload takes very long, the queue would get longer
  and longer. This option prevents the queue from growing without bound.
  When the queue reaches the given size, further mp4 files will be dropped.


`--notify-telegram bot_token chat_id`

- Use a Telegram bot to send messages to `chat_id`, when a detection edge occurs.


`--notify-queue-size 8`

- Detection edges are queued to wait for being processed. If sending Telegram
  messages takes very long, the queue would get longer and longer. This option
  prevents the queue from growing without bound. When the queue reaches the
  given size, further detection edges will be dropped.


`--log path`

- Path to log file. If not given, log messages are written to stdout.
- I use a timed-rotating log writer. Log files are split at midnight, and kept
  for 33 days, one file per day.


Utility Scripts
---------------

They are in directory `util/`.

`set_password.py`

- Generate a hash file to be used by the `--login` option of `pancake.py`


`limit_files_by_number.py`  
`limit_files_by_size.py`  

- Remove older files to prevent disk overfill. You will likely set up a cron job
  to apply one of them to the directories storing h264 and mp4 files.


`limit_days.py`

- Intended to be used on a remote backup location where files are organized into
  a YYYY/MM/DD directory tree, this script restricts the number of days present
  under each directory, and remove older days.


_Usage examples are given in each script._


Examples
--------

All examples assume this directory structure. The directories `h264`, `mp4`, and
`log` are created to house respective types of files.

```
.
├── pancake.py
├── postproc.py
├── index.html
├── login.html
├── static
├── h264
├── mp4
├── log
└── util
```

A lot of times, `pancake.py` and `postproc.py` are used in tandem. **Remember to
match unix domain socket and recording/conversion FPS.**

---------------------------------------------------------------------

```
python3 pancake.py \
    --record-continuous h264
```
- Record continuously into directory `h264`
- No post-processing

---------------------------------------------------------------------

```
python3 pancake.py \
    --record-continuous h264 \
    --emit ppp

python3 postproc.py ppp \
    --convert mp4 12 \
    --upload-after-convert 1 \
    --upload-scp pi@192.168.1.23 22 bak/cam1 \
    --log log/postproc.log
```
- Record continuously into directory `h264`
- Emit messages to unix domain socket for post-processor to handle
- Convert h264 into directory `mp4` at 12 FPS
- Trigger upload after each mp4 conversion
- SCP to `pi@192.168.1.23:bak/cam1`

---------------------------------------------------------------------

```
python3 pancake.py \
    --vflip \
    --hflip \
    --resolution 1280 720 \
    --fps 30 \
    --record-continuous h264 \
    --login password.txt \
    --emit ppp

python3 postproc.py ppp \
    --convert mp4 30 \
    --upload-after-convert 1 \
    --upload-scp pi@192.168.1.22 22 bak/cam2 \
    --upload-ftp 192.168.1.24 21 nick nickpass bakery/cam2 \
    --log log/postproc.log
```
- Vertically and horizontally flip the image
- Set resolution 1280x720, FPS 30. Note that this would make the recorded files
  quite large. _Disk space gets used up more quickly, mp4 conversion takes
  longer, upload takes longer._
- Record continuously into directory `h264`
- Password-protect the web page
- Emit messages to unix domain socket for post-processor to handle
- Convert h264 into directory `mp4` at 30 FPS
- Trigger upload after each mp4 conversion
- Upload to two destinations, one via SCP, one via FTP.

---------------------------------------------------------------------

```
python3 pancake.py \
    --record-detection edge h264 \
    --detect no-image \
    --emit ppp

python3 postproc.py ppp \
    --convert mp4 12 \
    --upload-minutes 0 20 40 \
    --upload-scp pi@192.168.1.22 22 bakery/cam3 \
    --upload-scp pi@192.168.1.23 22 bakery/cam3 \
    --log log/postproc.log
```
- Record detection edges into directory `h264`
- A `detect()` function is expected to be defined in `customize.py`
- Emit messages to unix domain socket for post-processor to handle
- Convert h264 into directory `mp4` at 12 FPS
- Trigger upload based on time, at 00-, 20-, 40-minute marks.
- Upload to two destinations, both via SCP.

---------------------------------------------------------------------

```
python3 pancake.py \
    --record-continuous h264 \
    --record-detection all h264 \
    --detect image \
    --detect-resize 224 224 \
    --emit ppp

python3 postproc.py ppp \
    --convert mp4 12 \
    --upload-after-convert 3 \
    --upload-minutes {5..59..15} \
    --upload-scp pi@192.168.1.22 22 bakery/cam4 \
    --log log/postproc.log
```
- Record continuously into directory `h264`
- Record detections for as long as detection remains true, into directory `h264`
- A `detect(bytes)` function is expected to be defined in `customize.py`
- Resize images to 224x224 before passing to detect function
- Emit messages to unix domain socket for post-processor to handle
- Convert h264 into directory `mp4` at 12 FPS
- Trigger upload after 3 mp4 conversions, or at 05-, 20-, 35-, 50-minute marks.
  Note that the shell expansion `{5..59..15}` may not be used in systemd service
  files. In that case, you have to write out the minutes explicitly.
- Upload via SCP


Systemd Service
---------------

These service files are mainly for my own reference. Remember to also set up
cron job to clean up files in directories `h264` and `mp4`.

```
[Unit]
Description=Raspberry Pancake CCTV
After=network.target

[Service]
WorkingDirectory=/home/pi/raspberry-pancake
User=pi
ExecStart=python3 pancake.py \
              --vflip \
              --hflip \
              --resolution 640 480 \
              --fps 12 \
              --record-continuous h264 \
              --login password.txt \
              --emit ppp

[Install]
WantedBy=multi-user.target
```

```
[Unit]
Description=Raspberry Pancake Post-Processor
After=network.target

[Service]
WorkingDirectory=/home/pi/raspberry-pancake
User=pi
ExecStart=python3 postproc.py ppp \
              --convert mp4 12 \
              --upload-after-convert 1 \
              --upload-scp pi@somewhere.on.planet 1234 bakery/oven \
              --log log/postproc.log

[Install]
WantedBy=multi-user.target
```