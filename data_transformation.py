from datavisualization import visualise_image
from torch.utils.data import Dataset
from tqdm.auto import tqdm
import os
from xml.etree import ElementTree as ET
import glob as glob
import torch
import cv2
import numpy as np
from xml.etree import ElementTree as et
import random
import albumentations as A
from albumentations.pytorch import ToTensorV2
from torchvision import transforms as transforms
import dill as pickle

def transform_data():
    # Define the training tranforms
    def resize(im, img_size=640, square=False):
        # Aspect ratio resize
        if square:
            im = cv2.resize(im, (img_size, img_size))
        else:
            h0, w0 = im.shape[:2]  # orig hw
            r = img_size / max(h0, w0)  # ratio
            if r != 1:  # if sizes are not equal
                im = cv2.resize(im, (int(w0 * r), int(h0 * r)))
        return im
    
    # Define the training tranforms
    def get_train_aug():
        return A.Compose([
            A.OneOf([
                A.Blur(blur_limit=3, p=0.5),
                A.MotionBlur(blur_limit=3, p=0.5),
                A.MedianBlur(blur_limit=3, p=0.5),
            ], p=0.5),
            A.ToGray(p=0.1),
            A.RandomBrightnessContrast(p=0.1),
            A.ColorJitter(p=0.1),
            A.RandomGamma(p=0.1),
            ToTensorV2(p=1.0),
        ], bbox_params=A.BboxParams(
            format='pascal_voc',
            label_fields=['labels'],
        ))
    
    def get_train_transform():
        return A.Compose([
            ToTensorV2(p=1.0),
        ], bbox_params=A.BboxParams(
            format='pascal_voc',
            label_fields=['labels'],
        ))
    
    def transform_mosaic(mosaic, boxes, img_size=640):
        """
        Resizes the `mosaic` image to `img_size` which is the desired image size
        for the neural network input. Also transforms the `boxes` according to the
        `img_size`.
    
        :param mosaic: The mosaic image, Numpy array.
        :param boxes: Boxes Numpy.
        :param img_resize: Desired resize.
        """
        aug = A.Compose(
            [A.Resize(img_size, img_size, always_apply=True, p=1.0)
        ])
        sample = aug(image=mosaic)
        resized_mosaic = sample['image']
        transformed_boxes = (np.array(boxes) / mosaic.shape[0]) * resized_mosaic.shape[1]
        for box in transformed_boxes:
            # Bind all boxes to correct values. This should work correctly most of
            # of the time. There will be edge cases thought where this code will
            # mess things up. The best thing is to prepare the dataset as well as 
            # as possible.
            if box[2] - box[0] <= 1.0:
                box[2] = box[2] + (1.0 - (box[2] - box[0]))
                if box[2] >= float(resized_mosaic.shape[1]):
                    box[2] = float(resized_mosaic.shape[1])
            if box[3] - box[1] <= 1.0:
                box[3] = box[3] + (1.0 - (box[3] - box[1]))
                if box[3] >= float(resized_mosaic.shape[0]):
                    box[3] = float(resized_mosaic.shape[0])
        return resized_mosaic, transformed_boxes
    
    # Define the validation transforms
    def get_valid_transform():
        return A.Compose([
            ToTensorV2(p=1.0),
        ], bbox_params=A.BboxParams(
            format='pascal_voc',
            label_fields=['labels'],
        ))


    class CustomDataset(Dataset):
        def __init__(
            self, 
            images_path, 
            labels_path, 
            img_size, 
            classes, 
            transforms=None, 
            use_train_aug=False,
            train=False, 
            mosaic=1.0,
            square_training=False
        ):
            self.transforms = transforms
            self.use_train_aug = use_train_aug
            self.images_path = images_path
            self.labels_path = labels_path
            self.img_size = img_size
            self.classes = classes
            self.train = train
            self.square_training = square_training
            self.mosaic_border = [-img_size // 2, -img_size // 2]
            self.image_file_types = ['*.jpg', '*.jpeg', '*.png', '*.ppm', '*.JPG']
            self.all_image_paths = []
            self.log_annot_issue_x = True
            self.mosaic = mosaic
            self.log_annot_issue_y = True
            
            # get all the image paths in sorted order
            for file_type in self.image_file_types:
                self.all_image_paths.extend(glob.glob(os.path.join(self.images_path, file_type)))
            self.all_annot_paths = glob.glob(os.path.join(self.labels_path, '*.xml'))
            self.all_images = [image_path.split(os.path.sep)[-1] for image_path in self.all_image_paths]
            self.all_images = sorted(self.all_images)
            # Remove all annotations and images when no object is present.
            self.read_and_clean()

        def read_and_clean(self):
            print('Checking Labels and images...')
            # Discard any image file when no annotation file is found.
            for image_name in tqdm(self.all_images, total=len(self.all_images)):
                possible_xml_name = os.path.join(self.labels_path, os.path.splitext(image_name)[0]+'.xml')
                if possible_xml_name not in self.all_annot_paths:
                    print(f"{possible_xml_name} not found...")
                    print(f"Removing {image_name} image")
                    self.all_images = [image_instance for image_instance in self.all_images if image_instance != image_name]

        def resize(self, im, square=False):
            if square:
                im = cv2.resize(im, (self.img_size, self.img_size))
            else:
                h0, w0 = im.shape[:2]  # orig hw
                r = self.img_size / max(h0, w0)  # ratio
                if r != 1:  # if sizes are not equal
                    im = cv2.resize(im, (int(w0 * r), int(h0 * r)))
            return im

        def load_image_and_labels(self, index):
            image_name = self.all_images[index]
            image_path = os.path.join(self.images_path, image_name)

            # Read the image.
            image = cv2.imread(image_path)
            # Convert BGR to RGB color format.
            image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB).astype(np.float32)
            image_resized = self.resize(image, square=self.square_training)
            image_resized /= 255.0
            
            # Capture the corresponding XML file for getting the annotations.
            annot_filename = os.path.splitext(image_name)[0] + '.xml'
            annot_file_path = os.path.join(self.labels_path, annot_filename)

            boxes = []
            orig_boxes = []
            labels = []
            
            # Get the height and width of the image.
            image_width = image.shape[1]
            image_height = image.shape[0]
                    
            # Box coordinates for xml files are extracted and corrected for image size given.
            # try:
            tree = et.parse(annot_file_path)
            root = tree.getroot()
            for member in root.findall('object'):
                # Map the current object name to `classes` list to get
                # the label index and append to `labels` list.
                labels.append(self.classes.index(member.find('name').text))
                
                # xmin = left corner x-coordinates
                xmin = float(member.find('bndbox').find('xmin').text)
                # xmax = right corner x-coordinates
                xmax = float(member.find('bndbox').find('xmax').text)
                # ymin = left corner y-coordinates
                ymin = float(member.find('bndbox').find('ymin').text)
                # ymax = right corner y-coordinates
                ymax = float(member.find('bndbox').find('ymax').text)

                xmin, ymin, xmax, ymax = self.check_image_and_annotation(
                    xmin, 
                    ymin, 
                    xmax, 
                    ymax, 
                    image_width, 
                    image_height, 
                    orig_data=True
                )

                orig_boxes.append([xmin, ymin, xmax, ymax])
                
                # Resize the bounding boxes according to the
                # desired `width`, `height`.
                xmin_final = (xmin/image_width)*image_resized.shape[1]
                xmax_final = (xmax/image_width)*image_resized.shape[1]
                ymin_final = (ymin/image_height)*image_resized.shape[0]
                ymax_final = (ymax/image_height)*image_resized.shape[0]

                xmin_final, ymin_final, xmax_final, ymax_final = self.check_image_and_annotation(
                    xmin_final, 
                    ymin_final, 
                    xmax_final, 
                    ymax_final, 
                    image_resized.shape[1], 
                    image_resized.shape[0],
                    orig_data=False
                )
                
                boxes.append([xmin_final, ymin_final, xmax_final, ymax_final])
            # except:
            #     pass
            # Bounding box to tensor.
            boxes_length = len(boxes)
            boxes = torch.as_tensor(boxes, dtype=torch.float32)
            # Area of the bounding boxes.
            area = (boxes[:, 3] - boxes[:, 1]) * (boxes[:, 2] - boxes[:, 0]) if boxes_length > 0 else torch.as_tensor(boxes, dtype=torch.float32)
            # No crowd instances.
            iscrowd = torch.zeros((boxes.shape[0],), dtype=torch.int64) if boxes_length > 0 else torch.as_tensor(boxes, dtype=torch.float32)
            # Labels to tensor.
            labels = torch.as_tensor(labels, dtype=torch.int64)
            return image, image_resized, orig_boxes, \
                boxes, labels, area, iscrowd, (image_width, image_height)

        def check_image_and_annotation(
            self, 
            xmin, 
            ymin, 
            xmax, 
            ymax, 
            width, 
            height, 
            orig_data=False
        ):
            """
            Check that all x_max and y_max are not more than the image
            width or height.
            """
            if ymax > height:
                ymax = height
            if xmax > width:
                xmax = width
            if xmax - xmin <= 1.0:
                if orig_data:
                    # print(
                        # '\n',
                        # '!!! xmax is equal to xmin in data annotations !!!'
                        # 'Please check data'
                    # )
                    # print(
                        # 'Increasing xmax by 1 pixel to continue training for now...',
                        # 'THIS WILL ONLY BE LOGGED ONCE',
                        # '\n'
                    # )
                    self.log_annot_issue_x = False
                xmin = xmin - 1
            if ymax - ymin <= 1.0:
                if orig_data:
                    # print(
                    #     '\n',
                    #     '!!! ymax is equal to ymin in data annotations !!!',
                    #     'Please check data'
                    # )
                    # print(
                    #     'Increasing ymax by 1 pixel to continue training for now...',
                    #     'THIS WILL ONLY BE LOGGED ONCE',
                    #     '\n'
                    # )
                    self.log_annot_issue_y = False
                ymin = ymin - 1
            return xmin, ymin, xmax, ymax


        def load_cutmix_image_and_boxes(self, index, resize_factor=512):
            """ 
            Adapted from: https://www.kaggle.com/shonenkov/oof-evaluation-mixup-efficientdet
            """
            s = self.img_size
            yc, xc = (int(random.uniform(-x, 2 * s + x)) for x in self.mosaic_border)  # mosaic center x, y
            indices = [index] + [random.randint(0, len(self.all_images) - 1) for _ in range(3)]

            # Create empty image with the above resized image.
            # result_image = np.full((h, w, 3), 1, dtype=np.float32)
            result_boxes = []
            result_classes = []

            for i, index in enumerate(indices):
                _, image_resized, orig_boxes, boxes, \
                labels, area, iscrowd, dims = self.load_image_and_labels(
                    index=index
                )

                h, w = image_resized.shape[:2]

                if i == 0:
                    # Create empty image with the above resized image.
                    result_image = np.full((s * 2, s * 2, image_resized.shape[2]), 114/255, dtype=np.float32)  # base image with 4 tiles
                    x1a, y1a, x2a, y2a = max(xc - w, 0), max(yc - h, 0), xc, yc  # xmin, ymin, xmax, ymax (large image)
                    x1b, y1b, x2b, y2b = w - (x2a - x1a), h - (y2a - y1a), w, h  # xmin, ymin, xmax, ymax (small image)
                elif i == 1:  # top right
                    x1a, y1a, x2a, y2a = xc, max(yc - h, 0), min(xc + w, s * 2), yc
                    x1b, y1b, x2b, y2b = 0, h - (y2a - y1a), min(w, x2a - x1a), h
                elif i == 2:  # bottom left
                    x1a, y1a, x2a, y2a = max(xc - w, 0), yc, xc, min(s * 2, yc + h)
                    x1b, y1b, x2b, y2b = w - (x2a - x1a), 0, max(xc, w), min(y2a - y1a, h)
                elif i == 3:  # bottom right
                    x1a, y1a, x2a, y2a = xc, yc, min(xc + w, s * 2), min(s * 2, yc + h)
                    x1b, y1b, x2b, y2b = 0, 0, min(w, x2a - x1a), min(y2a - y1a, h)
                result_image[y1a:y2a, x1a:x2a] = image_resized[y1b:y2b, x1b:x2b]
                padw = x1a - x1b
                padh = y1a - y1b

                if len(orig_boxes) > 0:
                    boxes[:, 0] += padw
                    boxes[:, 1] += padh
                    boxes[:, 2] += padw
                    boxes[:, 3] += padh

                    result_boxes.append(boxes)
                    result_classes += labels

            final_classes = []
            if len(result_boxes) > 0:
                result_boxes = np.concatenate(result_boxes, 0)
                np.clip(result_boxes[:, 0:], 0, 2 * s, out=result_boxes[:, 0:])
                result_boxes = result_boxes.astype(np.int32)
                for idx in range(len(result_boxes)):
                    if ((result_boxes[idx, 2] - result_boxes[idx, 0]) * (result_boxes[idx, 3] - result_boxes[idx, 1])) > 0:
                        final_classes.append(result_classes[idx])
                result_boxes = result_boxes[
                    np.where((result_boxes[:, 2] - result_boxes[:, 0]) * (result_boxes[:, 3] - result_boxes[:, 1]) > 0)
                ]
            # Resize the mosaic image to the desired shape and transform boxes.
            result_image, result_boxes = transform_mosaic(
                result_image, result_boxes, self.img_size
            )
            return result_image, torch.tensor(result_boxes), \
                torch.tensor(np.array(final_classes)), area, iscrowd, dims

        def __getitem__(self, idx):
            if not self.train: # No mosaic during validation.
                image, image_resized, orig_boxes, boxes, \
                    labels, area, iscrowd, dims = self.load_image_and_labels(
                    index=idx
                )

            if self.train: 
                mosaic_prob = random.uniform(0.0, 1.0)
                if self.mosaic >= mosaic_prob:
                    image_resized, boxes, labels, \
                        area, iscrowd, dims = self.load_cutmix_image_and_boxes(
                        idx, resize_factor=(self.img_size, self.img_size)
                    )
                else:
                    image, image_resized, orig_boxes, boxes, \
                        labels, area, iscrowd, dims = self.load_image_and_labels(
                        index=idx
                    )

            # Prepare the final `target` dictionary.
            target = {}
            target["boxes"] = boxes
            target["labels"] = labels
            target["area"] = area
            target["iscrowd"] = iscrowd
            image_id = torch.tensor([idx])
            target["image_id"] = image_id

            if self.use_train_aug: # Use train augmentation if argument is passed.
                train_aug = get_train_aug()
                sample = train_aug(image=image_resized,
                                        bboxes=target['boxes'],
                                        labels=labels)
                image_resized = sample['image']
                target['boxes'] = torch.Tensor(sample['bboxes']).to(torch.int64)
            else:
                sample = self.transforms(image=image_resized,
                                        bboxes=target['boxes'],
                                        labels=labels)
                image_resized = sample['image']
                target['boxes'] = torch.Tensor(sample['bboxes']).to(torch.int64)

            # Fix to enable training without target bounding boxes,
            # see https://discuss.pytorch.org/t/fasterrcnn-images-with-no-objects-present-cause-an-error/117974/4
            if np.isnan((target['boxes']).numpy()).any() or target['boxes'].shape == torch.Size([0]):
                target['boxes'] = torch.zeros((0, 4), dtype=torch.int64)
            return image_resized, target

        def __len__(self):
            return len(self.all_images)


    IMAGE_WIDTH = 640
    img_size = 640
    IMAGE_HEIGHT = 480
    classes = ["background","smoke"]

    train_dataset = CustomDataset(os.path.join(os.getcwd(),"object_detection_data/train/images"),os.path.join(os.getcwd(),"object_detection_data/train/annotations"),img_size, classes, get_train_transform(),use_train_aug=False, train=True, mosaic=1.0, square_training=False)
    valid_dataset = CustomDataset(os.path.join(os.getcwd(),"object_detection_data/valid/images"),os.path.join(os.getcwd(),"object_detection_data/valid/annotations"),img_size, classes, get_valid_transform(),train=False, square_training=False)

    i, a = train_dataset[0]
    print("iiiiii:",i)
    print("aaaaa:",a)
    with open('train_dataset.pkl', 'wb') as f:
        pickle.dump(train_dataset, f)
    with open('valid_dataset.pkl', 'wb') as f:
        pickle.dump(valid_dataset, f)

    return train_dataset

transform_data()
