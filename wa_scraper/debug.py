import cv2
import numpy as np

def show_image_from_bytes(image_bytes):
    np_array = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(np_array, cv2.IMREAD_COLOR)
    return image