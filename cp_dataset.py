#coding=utf-8
import torch
import torch.utils.data as data
import torchvision.transforms as transforms

from PIL import Image
from PIL import ImageDraw

import os.path as osp
import numpy as np
import json

class CPDataset(data.Dataset):
    """Dataset for CP-VTON."""
    def __init__(self, opt):
        super(CPDataset, self).__init__()
        # Base settings
        self.opt = opt
        self.root = opt.dataroot
        self.datamode = opt.datamode  # train or test or self-defined
        self.stage = opt.stage  # GMM or TOM
        self.data_list = opt.data_list
        self.fine_height = opt.fine_height
        self.fine_width = opt.fine_width
        self.radius = opt.radius
        self.data_path = osp.join(opt.dataroot, opt.datamode)
        self.transform = transforms.Compose([
            transforms.ToTensor(),
            transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))
        ])
        
        # Load data list
        im_names = []
        c_names = []
        with open(osp.join(opt.dataroot, opt.data_list), 'r') as f:
            for line in f.readlines():
                im_name, c_name = line.strip().split()
                im_names.append(im_name)
                c_names.append(c_name)

        self.im_names = im_names
        self.c_names = c_names

    def __len__(self):
        return len(self.im_names)

    def __getitem__(self, index):
        c_name = self.c_names[index]
        im_name = self.im_names[index]

        # Cloth image & mask
        if self.stage == 'GMM':
            c = Image.open(osp.join(self.data_path, 'cloth', c_name))
            cm = Image.open(osp.join(self.data_path, 'cloth-mask', c_name))
        else:
            c = Image.open(osp.join(self.data_path, 'warp-cloth', c_name))
            cm = Image.open(osp.join(self.data_path, 'warp-mask', c_name))

        # Ensure images are RGB
        if c.mode != 'RGB':
            c = c.convert('RGB')
        c = self.transform(c)  # [-1,1]
        
        cm_array = np.array(cm)
        cm_array = (cm_array >= 128).astype(np.float32)
        cm = torch.from_numpy(cm_array)  # [0,1]
        cm.unsqueeze_(0)

        # Person image
        im = Image.open(osp.join(self.data_path, 'image', im_name))
        if im.mode != 'RGB':
            im = im.convert('RGB')
        im = self.transform(im)  # [-1,1]

        # Parsing image
        parse_name = im_name.replace('.jpg', '.png')
        im_parse = Image.open(osp.join(self.data_path, 'image-parse', parse_name))
        parse_array = np.array(im_parse)
        parse_shape = (parse_array > 0).astype(np.float32)
        parse_head = (parse_array == 1).astype(np.float32) + \
                     (parse_array == 2).astype(np.float32) + \
                     (parse_array == 4).astype(np.float32) + \
                     (parse_array == 13).astype(np.float32)
        parse_cloth = (parse_array == 5).astype(np.float32) + \
                      (parse_array == 6).astype(np.float32) + \
                      (parse_array == 7).astype(np.float32)

        # Shape downsample
        parse_shape = Image.fromarray((parse_shape * 255).astype(np.uint8))
        parse_shape = parse_shape.resize((self.fine_width // 16, self.fine_height // 16), Image.BILINEAR)
        parse_shape = parse_shape.resize((self.fine_width, self.fine_height), Image.BILINEAR)

        # Convert to RGB to match self.transform expectations
        parse_shape = parse_shape.convert('RGB')
        shape = self.transform(parse_shape)  # [-1,1]

        # Ensure `shape` has 1 channel
        if shape.size(0) != 1:
            shape = shape[0:1, :, :]  # Take the first channel only

        phead = torch.from_numpy(parse_head)  # [0,1]
        pcm = torch.from_numpy(parse_cloth)  # [0,1]

        # Upper cloth
        im_c = im * pcm + (1 - pcm)  # [-1,1], fill 1 for other parts
        im_h = im * phead - (1 - phead)  # [-1,1], fill 0 for other parts

        # Pose points
        pose_name = im_name.replace('.jpg', '_keypoints.json')
        with open(osp.join(self.data_path, 'pose', pose_name), 'r') as f:
            pose_label = json.load(f)
            pose_data = pose_label['people'][0]['pose_keypoints']
            pose_data = np.array(pose_data).reshape((-1, 3))

        point_num = pose_data.shape[0]
        pose_map = torch.zeros(18, self.fine_height, self.fine_width)  # Ensure 18 channels
        r = self.radius
        im_pose = Image.new('L', (self.fine_width, self.fine_height))
        pose_draw = ImageDraw.Draw(im_pose)
        for i in range(min(18, point_num)):  # Ensure up to 18 keypoints
            one_map = Image.new('L', (self.fine_width, self.fine_height))
            draw = ImageDraw.Draw(one_map)
            pointx, pointy = pose_data[i, :2]
            if pointx > 1 and pointy > 1:
                draw.rectangle((pointx - r, pointy - r, pointx + r, pointy + r), 'white', 'white')

            # Convert one_map to RGB
            one_map = one_map.convert('RGB')

            # Apply transform
            one_map = self.transform(one_map)
            pose_map[i] = one_map[0]

        # Convert im_pose to RGB and transform
        im_pose = im_pose.convert('RGB')
        im_pose = self.transform(im_pose)

        # Debugging: Ensure channel counts are correct
        print(f"Shape: {shape.size()}, im_h: {im_h.size()}, pose_map: {pose_map.size()}")

        # Concatenate tensors to form agnostic representation
        agnostic = torch.cat([shape, im_h, pose_map], 0)

        if self.stage == 'GMM':
            im_g = Image.open('grid.png')
            im_g = self.transform(im_g)
        else:
            im_g = ''

        result = {
            'c_name': c_name,
            'im_name': im_name,
            'cloth': c,
            'cloth_mask': cm,
            'image': im,
            'agnostic': agnostic,
            'parse_cloth': im_c,
            'shape': shape,
            'head': im_h,
            'pose_image': im_pose,
            'grid_image': im_g,
        }

        return result


class CPDataLoader(object):
    def __init__(self, opt, dataset):
        super(CPDataLoader, self).__init__()

        if opt.shuffle:
            train_sampler = torch.utils.data.sampler.RandomSampler(dataset)
        else:
            train_sampler = None

        self.data_loader = torch.utils.data.DataLoader(
            dataset,
            batch_size=opt.batch_size,
            shuffle=(train_sampler is None),
            num_workers=opt.workers,
            pin_memory=True,
            sampler=train_sampler,
        )
        self.dataset = dataset
        self.data_iter = iter(self.data_loader)

    def next_batch(self):
        try:
            batch = next(self.data_iter)
        except StopIteration:
            self.data_iter = iter(self.data_loader)
            batch = next(self.data_iter)

        return batch


if __name__ == "__main__":
    print("Check the dataset for geometric matching module!")
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataroot", default="data")
    parser.add_argument("--datamode", default="train")
    parser.add_argument("--stage", default="GMM")
    parser.add_argument("--data_list", default="train_pairs.txt")
    parser.add_argument("--fine_width", type=int, default=192)
    parser.add_argument("--fine_height", type=int, default=256)
    parser.add_argument("--radius", type=int, default=3)
    parser.add_argument("--shuffle", action='store_true', help='shuffle input data')
    parser.add_argument('-b', '--batch-size', type=int, default=4)
    parser.add_argument('-j', '--workers', type=int, default=1)
    
    opt = parser.parse_args()
    dataset = CPDataset(opt)
    data_loader = CPDataLoader(opt, dataset)

    print('Size of the dataset: %05d, dataloader: %04d' \
          % (len(dataset), len(data_loader.data_loader)))
    first_item = dataset.__getitem__(0)
    first_batch = data_loader.next_batch()
