import hashlib
import getpass

p = getpass.getpass()

with open('password.txt', 'w') as f:
    f.write(hashlib.sha512(p.encode()).hexdigest())

print('Saved to password.txt')
