# PL-YOLO

PL-YOLO is a lightweight model for small object detection. Related [paper]() are available here.

![result](G:\result1.png)

## Download

```bash
$ git clone https://github.com/painlove1999/PL-YOLO
```

## Train

***The training process is referred to yolov5, and we increased the weight of PL-YOLO on the Dota dataset***

```bash
python train.py --data DOTA.yaml --cfg models/PL-YOLO.yaml --weights 'weights/PL-YOLO.pt' --batch-size 1                                                                                                18                                                                                              32
```

## Inference 

```bash
python detect.py --source 0  # webcam    
                          img.jpg  # image
                          vid.mp4  # video
                          path/  # directory
                          path/*.jpg  # glob
                          'https://youtu.be/Zgi9g1ksQHc'  # YouTube
                          'rtsp://example.com/media.mp4'  # RTSP, RTMP, HTTP stream
```

## Results

***We compare the results with other state-of-the-art end-to-end target detectors and show the results for the Dota dataset***

| Method         | **Params(M)** | **GFLOPs** | **FPS@RTX 3060** | AP    | AP50  | AP75  |
| -------------- | ---------- | ---------- | ---------------- | ----- | ----- | ----- |
| **Yolov5l**    | 46.21     | 108.2      | 15               | 48.3% | 72.9% | 51.2% |
| **Yolov5m**    | 20.09     | 48.2       | 25               | 47.1% | 71.8% | 49.0% |
| **Yolov5s**    | 7.06      | 16.0       | 65               | 43.4% | 68.8% | 46.4% |
| **Yolov5n**    | 1.78      | 4.2        | 121              | 38.0% | 64.1% | 39.1% |
| **YOLOXl**     | 54.16 | 155.6 | 13 | 47.3% | 71.3% | 49.6% |
| **YOLOXm**     | 25.29 | 73.8 | 21 | 46.8% | 70.7% | 47.9% |
| **YOLOXs**     | 8.94 | 26.8 | 49 | 43.2% | 67.7% | 46.1% |
| **YOLOX-tiny** | 5.04 | 15.16 | 58 | 41.8% | 66.0% | 44.5% |
| **YOLOX-Nano** | 2.24 | 6.89 | 89 | 38.0% | 62.1% | 38.8% |
| **YOLOv4-p6** | 126.8 | 178.3 | 8 | 48.5% | 72.7% | 52.8% |
| **YOLOv4-p5** | 70.3 | 156.7 | 10 | 48.0% | 72.1% | 51.0% |
| **YOLOv4-csp** | 52.57 | 119.5 | 14 | 47.8% | 71.4% | 51.9% |
| **YOLOv3** | 61.59 | 155.3 | 25 | 45.4% | 70.5% | 47.1% |
| **YOLOv3-spp** | 62.64 | 156.2 | 24 | 46.1% | 71.0% | 47.9% |
| **YOLOv5-Liteg** | 5.45 | 15.2 | 89 | 36.3% | 59.3% | 36.9% |
| **YOLOv5-Litec** | 4.41 | 8.7 | 100 | 31.6% | 51.8% | 29.5% |
| **YOLOv5-Lites** | 1.56 | 3.7 | 105 | 28.2% | 47.2% | 28.7% |
| **YOLOv4-tiny** | 6.15 | 19.2 | 76 | 38.7% | 61.0% | ---- |
| **YOLOv3-tiny** | 8.70 | 13.0 | 100 | 34.8% | 58.2% | ---- |
| **PL-YOLO** | 4.63 | 10.5 | 90 | 42.0% | 66.0% | 45.0% |
| **PL-YOLO(832)** | --- | ---- | 121 | 40.5% | 64.0% | 43.3% |
| **PL-YOLO(640)** | ---- | ----- | 162 | 37.4% | 60.0% | 38.0% |

## Acknowledgements

+ [https://github.com/ultralytics/yolov5](https://github.com/ultralytics/yolov5)

