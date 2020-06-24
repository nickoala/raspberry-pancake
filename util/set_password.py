import hashlib
import getpass

"""
$ python3 set_password.py

Ask for a password, then save the hash to password.txt
"""

p = getpass.getpass()

with open('password.txt', 'w') as f:
    f.write(hashlib.sha512(p.encode()).hexdigest())

print('Saved to password.txt')
