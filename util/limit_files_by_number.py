import sys
import os
import glob

""" Usage:
$ python3 limit_files_by_number.py dirA 8 dirB 8
"""

args = sys.argv[1:]

pairs = [(args[i], int(args[i+1])) for i in range(0, len(args), 2)]

for dir, number_to_keep in pairs:
    # Sort files from newest to oldest
    files = sorted(glob.iglob(os.path.join(dir, '*')),
                key=lambda x: os.stat(x).st_mtime,
                reverse=True)

    # Remove oldest files
    for f in files[number_to_keep:]:
        os.remove(f)
