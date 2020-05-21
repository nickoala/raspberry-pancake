from datetime import datetime

def detect():
    return (0 <= datetime.now().second <= 10)

# def detect():
#     return (25 <= datetime.now().minute <= 35)

# def detect(bytes):
#     return (0 <= datetime.now().second <= 10)

# def detect(bytes):
#     return (33 <= datetime.now().minute <= 55)
