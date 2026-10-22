"""Fetch OpenCV Zoo weights at build time and verify their published LFS digests."""
import hashlib
import os
from pathlib import Path
from urllib.request import urlopen

MODELS = {
    "face_detection_yunet/face_detection_yunet_2023mar.onnx":
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    "face_recognition_sface/face_recognition_sface_2021dec.onnx":
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
}


def main():
    target = Path(os.getenv("FACE_MODEL_DIR", "/app/face_models"))
    target.mkdir(parents=True, exist_ok=True)
    for name, expected in MODELS.items():
        destination = target / Path(name).name
        if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest() == expected:
            continue
        url = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/" + name
        with urlopen(url, timeout=120) as response:
            data = response.read(45 * 1024 * 1024)
        if hashlib.sha256(data).hexdigest() != expected:
            raise RuntimeError("Unexpected model digest: " + name)
        destination.write_bytes(data)


if __name__ == "__main__":
    main()
