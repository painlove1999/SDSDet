# YOLOv5 🚀 by Ultralytics, GPL-3.0 license
"""
Common modules
"""

import json
import math
import platform
import warnings
from collections import OrderedDict, namedtuple
from copy import copy
from pathlib import Path

import cv2
import matplotlib.pyplot
import numpy as np
import pandas as pd
import requests
import torch
import torch.nn as nn
from PIL import Image
from torch.cuda import amp
import torch.nn.functional as F
from utils.datasets import exif_transpose, letterbox
from utils.general import (LOGGER, check_requirements, check_suffix, colorstr, increment_path, make_divisible,
                           non_max_suppression, scale_coords, xywh2xyxy, xyxy2xywh)
from utils.plots import Annotator, colors, save_one_box
from utils.torch_utils import copy_attr, time_sync
import matplotlib.pyplot as plt

matplotlib.use('TkAgg')
def autopad(k, p=None):  # kernel, padding
    # Pad to 'same'
    if p is None:
        p = k // 2 if isinstance(k, int) else [x // 2 for x in k]  # auto-pad
    return p


class Conv_Repli1(nn.Module):
    # Standard convolution
    def __init__(self, c1, c2, k=1, s=1, p=None, g=1, act=True):  # ch_in, ch_out, kernel, stride, padding, groups
        super().__init__()
        self.conv = nn.Conv2d(c1, c2, k, s, autopad(k, p), groups=g, bias=False)
        self.bn = nn.BatchNorm2d(c2)
        self.act = nn.SiLU() if act is True else (act if isinstance(act, nn.Module) else nn.Identity())

    def forward(self, x):
        return self.act(self.bn(self.conv(x)))

    def forward_fuse(self, x):
        return self.act(self.conv(x))

class Conv(nn.Module):
    # Standard convolution
    def __init__(self, c1, c2, k=1, s=1, p=None, g=1, act=True):  # ch_in, ch_out, kernel, stride, padding, groups
        super().__init__()
        self.conv = nn.Conv2d(c1, c2, k, s, autopad(k,p),groups=g, bias=False)
        self.bn = nn.BatchNorm2d(c2)
        self.act = nn.SiLU() if act is True else (act if isinstance(act, nn.Module) else nn.Identity())


    def forward(self, x):
        return self.act(self.bn(self.conv(x)))

    def forward_fuse(self, x):
        return self.act(self.conv(x))


class Conv_Repli(nn.Module):
    # Standard convolution
    def __init__(self, c1, c2, k=1, s=1, p=None, g=1, act=True):  # ch_in, ch_out, kernel, stride, padding, groups
        super().__init__()
        self.conv = nn.Conv2d(c1, c2, k, s, groups=g, bias=False)
        self.bn = nn.BatchNorm2d(c2)
        self.act = nn.SiLU() if act is True else (act if isinstance(act, nn.Module) else nn.Identity())
        if k==1:
          self.padding=nn.Identity()

        if not k==1:

          # self.padding = nn.ReflectionPad2d(padding=tuple([autopad(k, p) for _ in range(4)]))
          self.padding=nn.ReplicationPad2d(padding=tuple([autopad(k,p) for _ in range(4)]))

    def forward(self, x):
        return self.act(self.bn(self.conv(self.padding(x))))

    def forward_fuse(self, x):
        return self.act(self.conv(self.padding(x)))

class DWConv(Conv):
    # Depth-wise convolution class
    def __init__(self, c1, c2, k=1, s=1, act=True):  # ch_in, ch_out, kernel, stride, padding, groups
        super().__init__(c1, c2, k, s, g=math.gcd(c1, c2), act=act)


class TransformerLayer(nn.Module):
    # Transformer layer https://arxiv.org/abs/2010.11929 (LayerNorm layers removed for better performance)
    def __init__(self, c, num_heads):
        super().__init__()
        self.q = nn.Linear(c, c, bias=False)
        self.k = nn.Linear(c, c, bias=False)
        self.v = nn.Linear(c, c, bias=False)
        self.ma = nn.MultiheadAttention(embed_dim=c, num_heads=num_heads)
        self.fc1 = nn.Linear(c, c, bias=False)
        self.fc2 = nn.Linear(c, c, bias=False)

    def forward(self, x):
        x = self.ma(self.q(x), self.k(x), self.v(x))[0] + x
        x = self.fc2(self.fc1(x)) + x
        return x


class TransformerBlock(nn.Module):
    # Vision Transformer https://arxiv.org/abs/2010.11929
    def __init__(self, c1, c2, num_heads, num_layers):
        super().__init__()
        self.conv = None
        if c1 != c2:
            self.conv = Conv(c1, c2)
        self.linear = nn.Linear(c2, c2)  # learnable position embedding
        self.tr = nn.Sequential(*(TransformerLayer(c2, num_heads) for _ in range(num_layers)))
        self.c2 = c2

    def forward(self, x):
        if self.conv is not None:
            x = self.conv(x)
        b, _, w, h = x.shape
        p = x.flatten(2).permute(2, 0, 1)
        return self.tr(p + self.linear(p)).permute(1, 2, 0).reshape(b, self.c2, w, h)


class Bottleneck(nn.Module):
    # Standard bottleneck
    def __init__(self, c1, c2, shortcut=True, g=1, e=0.5):  # ch_in, ch_out, shortcut, groups, expansion
        super().__init__()
        c_ = int(c2 * e)  # hidden channels
        self.cv1 = Conv(c1, c_, 1, 1)
        self.cv2 = Conv(c_, c2, 3, 1, g=g)
        self.add = shortcut and c1 == c2

    def forward(self, x):
        return x + self.cv2(self.cv1(x)) if self.add else self.cv2(self.cv1(x))


class BottleneckCSP(nn.Module):
    # CSP Bottleneck https://github.com/WongKinYiu/CrossStagePartialNetworks
    def __init__(self, c1, c2, n=1, shortcut=True, g=1, e=0.5):  # ch_in, ch_out, number, shortcut, groups, expansion
        super().__init__()
        c_ = int(c2 * e)  # hidden channels
        self.cv1 = Conv(c1, c_, 1, 1)
        self.cv2 = nn.Conv2d(c1, c_, 1, 1, bias=False)
        self.cv3 = nn.Conv2d(c_, c_, 1, 1, bias=False)
        self.cv4 = Conv(2 * c_, c2, 1, 1)
        self.bn = nn.BatchNorm2d(2 * c_)  # applied to cat(cv2, cv3)
        self.act = nn.SiLU()
        self.m = nn.Sequential(*(Bottleneck(c_, c_, shortcut, g, e=1.0) for _ in range(n)))

    def forward(self, x):
        y1 = self.cv3(self.m(self.cv1(x)))
        y2 = self.cv2(x)
        return self.cv4(self.act(self.bn(torch.cat((y1, y2), dim=1))))


class C3(nn.Module):
    # CSP Bottleneck with 3 convolutions
    def __init__(self, c1, c2, n=1, shortcut=True, g=1, e=0.5):  # ch_in, ch_out, number, shortcut, groups, expansion
        super().__init__()
        c_ = int(c2 * e)  # hidden channels
        self.cv1 = Conv(c1, c_, 1, 1)
        self.cv2 = Conv(c1, c_, 1, 1)
        self.cv3 = Conv(2 * c_, c2, 1)  # act=FReLU(c2)
        self.m = nn.Sequential(*(Bottleneck(c_, c_, shortcut, g, e=1.0) for _ in range(n)))
        # self.m = nn.Sequential(*[CrossConv(c_, c_, 3, 1, g, 1.0, shortcut) for _ in range(n)])

    def forward(self, x):
        return self.cv3(torch.cat((self.m(self.cv1(x)), self.cv2(x)), dim=1))


class C3TR(C3):
    # C3 module with TransformerBlock()
    def __init__(self, c1, c2, n=1, shortcut=True, g=1, e=0.5):
        super().__init__(c1, c2, n, shortcut, g, e)
        c_ = int(c2 * e)
        self.m = TransformerBlock(c_, c_, 4, n)


class C3SPP(C3):
    # C3 module with SPP()
    def __init__(self, c1, c2, k=(5, 9, 13), n=1, shortcut=True, g=1, e=0.5):
        super().__init__(c1, c2, n, shortcut, g, e)
        c_ = int(c2 * e)
        self.m = SPP(c_, c_, k)





class SPP(nn.Module):
    # Spatial Pyramid Pooling (SPP) layer https://arxiv.org/abs/1406.4729
    def __init__(self, c1, c2, k=(5, 9, 13)):
        super().__init__()
        c_ = c1 // 2  # hidden channels
        self.cv1 = Conv(c1, c_, 1, 1)
        self.cv2 = Conv(c_ * (len(k) + 1), c2, 1, 1)
        self.m = nn.ModuleList([nn.MaxPool2d(kernel_size=x, stride=1, padding=x // 2) for x in k])

    def forward(self, x):
        x = self.cv1(x)
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')  # suppress torch 1.9.0 max_pool2d() warning
            return self.cv2(torch.cat([x] + [m(x) for m in self.m], 1))


class SPPF(nn.Module):
    # Spatial Pyramid Pooling - Fast (SPPF) layer for YOLOv5 by Glenn Jocher
    def __init__(self, c1, c2, k=5):  # equivalent to SPP(k=(5, 9, 13))
        super().__init__()
        c_ = c1 // 2  # hidden channels
        self.cv1 = Conv(c1, c_, 1, 1)
        self.cv2 = Conv(c_ * 4, c2, 1, 1)
        self.m = nn.MaxPool2d(kernel_size=k, stride=1, padding=k // 2)

    def forward(self, x):
        x = self.cv1(x)
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')  # suppress torch 1.9.0 max_pool2d() warning
            y1 = self.m(x)
            y2 = self.m(y1)
            return self.cv2(torch.cat([x, y1, y2, self.m(y2)], 1))


class Focus(nn.Module):
    # Focus wh information into c-space
    def __init__(self, c1, c2, k=1, s=1, p=None, g=1, act=True):  # ch_in, ch_out, kernel, stride, padding, groups
        super().__init__()
        self.conv = Conv(c1 * 4, c2, k, s, p, g, act)
        # self.contract = Contract(gain=2)

    def forward(self, x):  # x(b,c,w,h) -> y(b,4c,w/2,h/2)
        return self.conv(torch.cat([x[..., ::2, ::2], x[..., 1::2, ::2], x[..., ::2, 1::2], x[..., 1::2, 1::2]], 1))
        # return self.conv(self.contract(x))







# /////////////////
class C3_rep(nn.Module):
    # CSP Bottleneck with 3 convolutions
    def __init__(self, c1, c2, n=1, shortcut=True, g=1, e=0.5):  # ch_in, ch_out, number, shortcut, groups, expansion
        super().__init__()
        c_ = int(c2 * e)  # hidden channels
        self.cv1 = Conv(c1, c_, 1, 1)
        self.cv2 = Conv(c1, c_, 1, 1)
        self.cv3 = Conv(2 * c_, c2, 1)  # act=FReLU(c2)
        self.m = nn.Sequential(*(Bottleneck_rep(c_, c_, shortcut, g, e=1.0) for _ in range(n)))
        # self.m = nn.Sequential(*[CrossConv(c_, c_, 3, 1, g, 1.0, shortcut) for _ in range(n)])

    def forward(self, x):
        return self.cv3(torch.cat((self.m(self.cv1(x)), self.cv2(x)), dim=1))

class SElayer(nn.Module):

    def __init__(self, channel, reduction=16):
        super(SElayer, self).__init__()
        self.avg_pool = torch.nn.AdaptiveAvgPool2d(1)
        self.fc = nn.Sequential(
            nn.Linear(channel, channel // reduction, bias=False),
            nn.ReLU(inplace=True),
            nn.Linear(channel // reduction, channel, bias=False),
            nn.Sigmoid()
        )

    def forward(self, x):
        b, c, _, _ = x.size()
        y = self.avg_pool(x).view(b, c)
        y = self.fc(y).view(b, c, 1, 1)
        return x * y.expand_as(x)

class Bottleneckcsp_rep1(nn.Module):
    def __init__(self,c1, c2, n=1, shortcut=True, g=1, e=0.5):
        super().__init__()
        c_ = int(c2 * e)  # hidden channels
        self.cv1 = Conv_Re(c1, c_, 1, 1)
        self.cv2 = Conv_Re(c1, c_, 1, 1)
        self.cv3 = Conv_Re(2 * c_, c2, 1)
        self.m = nn.Sequential(*(Bottleneck_rep(c_, c_, shortcut, g, e=1.0) for _ in range(n)))

    def forward(self, x):
        x1=self.cv2(x)
        x2=self.cv1(x)
        return self.cv3(torch.cat((self.m(x1), x2), dim=1))


class Bottleneckcsp_rep(nn.Module):
    def __init__(self,c1, c2, n=1, shortcut=True, g=1, e=0.5):
        super().__init__()
        c_ = int(c2 * e)  # hidden channels
        self.cv1 = Conv(c1, c_, 1, 1)
        self.cv2 = Conv(c1, c_, 1, 1)
        self.cv3 = Conv(2 * c_, c2, 1)
        self.m = nn.Sequential(*(Bottleneck_rep(c_, c_, shortcut, g, e=1.0) for _ in range(n)))

    def forward(self, x):
        x1=self.cv2(x)
        x2=self.cv1(x)
        return self.cv3(torch.cat((self.m(x1)+x1, x2), dim=1))

class DCL_UP(nn.Module):
    def __init__(self,c1,c2):
        super().__init__()
        self.cv1=Conv_Re(c1, c2 // 2, k=3, s=1, g=c2 // 2)
        self.cv2 = nn.Sequential(Conv(c1, c2//2, k=1, s=1), Conv_Re(c2 // 2, c2 // 2, k=3, s=1))
        self.cv3 = Conv(c2, c2, k=1, s=1)
    def forward(self,x):

        return self.cv3(torch.cat([self.cv2(x)+x,self.cv1(x)],dim=1))

class Bottleneck_rep(nn.Module):
    # Standard bottleneck
    def __init__(self, c1, c2, shortcut=True, g=1, e=1):  # ch_in, ch_out, shortcut, groups, expansion
        super().__init__()
        c_ = int(c2 * e)  # hidden channels
        self.cv1 = Conv(c1, c_, 1, 1)
        self.cv2 = Conv_Re(c_, c2, 3, 1, g=g)
        self.add = shortcut and c1 == c2

    def forward(self, x):
        return x + self.cv2(self.cv1(x)) if self.add else self.cv2(self.cv1(x))

class Bottleneckcsps(nn.Module):
    def __init__(self,c1, c2, n=1, shortcut=True, g=1, e=0.5):
        super().__init__()
        c_ = int(c2 * e)  # hidden channels
        self.cv1 = Conv(c1, c_, 1, 1)
        self.cv2 = Conv(c1, c_, 1, 1)
        self.cv3 = Conv(2 * c_, c2, 1)
        self.m = nn.Sequential(*(Bottleneck(c_, c_, shortcut, g, e=1.0) for _ in range(n)))

    def forward(self, x):
        x1=self.cv2(x)
        x2=self.cv1(x)
        return self.cv3(torch.cat((self.m(x1)+x1, x2), dim=1))

class NEM(nn.Module):

    def __init__(self):
        super().__init__()
        self.act=nn.Sigmoid()
        self.up=nn.Upsample(scale_factor=2)

    def forward(self,x):
        x1,x2=x



        x2=self.act(self.up(x2).mean(dim=1,keepdim=True))


        x=x1-x1*x2

        return x

class OGF_NEM(nn.Module):

    def __init__(self):
        super().__init__()
        self.act=nn.Sigmoid()
        self.up=nn.Upsample(scale_factor=2)

    def forward(self,x):
        x1,x2=x
        # w1=(torch.sum((x1).cpu(), dim=1) / x1.shape[1]).squeeze().cpu()
        #
        # w2= (torch.sum((x2).cpu(), dim=1) / x2.shape[1]).squeeze().cpu()

        x2=x2.detach()
        x2=self.act(self.up(x2).mean(dim=1,keepdim=True))
        # w6= (torch.sum((x2).cpu(), dim=1) / (x2).shape[1]).squeeze().cpu()
        # w3=(torch.sum((x1*x2).cpu(), dim=1) / (x1*x2).shape[1]).squeeze().cpu()
        # w4=(torch.sum(x1.cpu(), dim=1) / (x1).shape[1]).squeeze().cpu()
        #
        x=x1-x1*x2
        # w5= (torch.sum(x.cpu(), dim=1) / (x).shape[1]).squeeze().cpu()
        #
        # fig, ax = plt.subplots(1,6, tight_layout=False)  # 8 rows x n/8 cols
        # ax = ax.ravel()
        # ax[0].imshow(w1)  # cmap='gray'
        # ax[0].axis('off')
        # ax[1].imshow(w2)  # cmap='gray'
        # ax[1].axis('off')
        # ax[2].imshow(w3)  # cmap='gray'
        # ax[2].axis('off')
        # ax[3].imshow(w4)  # cmap='gray'
        # ax[3].axis('off')
        # ax[4].imshow(w5)  # cmap='gray'
        # ax[4].axis('off')
        # ax[5].imshow(w6)  # cmap='gray'
        # ax[5].axis('off')
        # plt.savefig("zky", dpi=300, bbox_inches='tight')
        return x

class ClassificationModel(nn.Module):
    def __init__(self,c1,c2,na):
        super().__init__()
        self.cv1=Conv(c1,c1,k=3,s=1)
        self.cls_preds=nn.Conv2d(c1, c2, kernel_size=1, stride=1)
        self.na=na

    def forward(self,x):
        b,c,h,w=x.shape
        return self.cls_preds(self.cv1(x)).view(b, self.na, -1, h, w).permute(0, 1, 3, 4, 2).contiguous()

    def _initialize_biases(self,s,nc,cf=None):
        b = self.cls_preds.bias.view(self.na, -1)
        b.data += math.log(0.6 / (nc - 0.99)) if cf is None else torch.log(cf / cf.sum())  # cls
        self.cls_preds.bias.bias = torch.nn.Parameter(b.view(-1), requires_grad=True)

class RegressionModel(nn.Module):
    def __init__(self,c1,c2,na):
        super(RegressionModel, self).__init__()
        self.na=na
        self.cv1 = Conv(c1, c1, k=3, s=1)
        self.reg_preds = nn.Conv2d(c1, c2-na, kernel_size=1, stride=1)
        self.obj_preds=nn.Conv2d(c1,na,kernel_size=1, stride=1)
        pass

    def forward(self, x):
        b,c,h,w=x.shape
        x=self.cv1(x)
        x1=self.reg_preds(x)
        x2=self.obj_preds(x)
        x1=x1.view(b, self.na,-1, h, w).permute(0, 1, 3, 4, 2).contiguous()
        x2=x2.view(b, self.na,-1, h, w).permute(0, 1, 3, 4, 2).contiguous()
        return torch.cat([x1,x2],dim=-1)

    def _initialize_biases(self,s):
        b = self.obj_preds.bias.view(self.na, -1)  # conv.bias(255) to (3,85)
        b.data[:,] += math.log(8 / (1024 / s) ** 2)  # obj (8 objects per 640 image)
        self.obj_preds.bias = torch.nn.Parameter(b.view(-1), requires_grad=True)



class Decoupled_head(nn.Module):
    def __init__(self, c1, no, na, ch):
        super().__init__()
        # c1:Number of input channels
        # no:Dimension per anchor replication
        # na:Number of anchor
        # ch:Dimensionality of NECK output
        self.no = no
        self.nc = no - 5
        c2 = int(torch.tensor(ch, dtype=torch.float).mean()) if (len(ch) % 2) == 0 else ch[int(len(ch) / 2)]

        c2 = c2 if c1 > c2 else c1
        self.stem = Conv(c1,c2,k=1,s=1)
        self.class_=ClassificationModel(c2,self.nc*na,na)
        self.Reg_=RegressionModel(c2,5*na,na)

    def forward(self,x):
        x=self.stem(x)
        return torch.cat([self.Reg_(x),self.class_(x)],dim=-1)

    def _initialize_biases(self,s, cf=None):

        self.class_._initialize_biases(s,self.nc,cf)
        self.Reg_._initialize_biases(s)

class DCL1(nn.Module):
    def __init__(self,c1,c2,shortcut=True):
        super().__init__()
        self.add=shortcut
        c_=int(c2*0.5)
        self.conv1=nn.Sequential(Conv(c1,c_,k=1,s=1), Conv_Re(c_, c_, k=3, s=1))
        self.conv2=nn.Sequential(Conv(c1,c_,k=1,s=1), Conv_Re(c_, c_, k=3, s=1, g=c_))
        self.conv3=Conv(c1,c2,k=1,s=1)


    def forward(self,x):
        # x1,x2=x.chunk(2,dim=1)
        x1=self.conv1(x)
        x2=self.conv2(x)
        x3=torch.cat([x1,x2],dim=1)
        x3=x3+x if self.add else x3
        x3=self.conv3(x3)
        return x3


class DCL2(nn.Module):
    def __init__(self,c1,c2,shortcut=False):
        super(DCL, self).__init__()
        self.add=shortcut
        c_=int(c1*0.5)
        c_2=int(c1-c_)
        self.cv1=Conv(c_2, c_2, k=3, s=1)
        self.cv2=nn.Sequential(Conv(c_,c_,k=1,s=1), DWConv(c_, c_, k=3, s=1))
        self.cv3=Conv(c1,c2,k=1,s=1)


    def forward(self,x):
        x1,x2=x.chunk(2,dim=1)
        x1=self.cv1(x1)
        x2=self.cv2(x2)
        x3=torch.cat([x1,x2],dim=1)
        x3=x3+x if self.add else x3
        x3=self.cv3(x3)
        return x3

class FC(nn.Module):
    def __init__(self,c1,c2,shortcut=True):
        super(FC, self).__init__()
        self.add=shortcut
        self.cv1=Conv(c1,c2,k=1,s=1)
        self.cv2=Conv_Repli(c2, c2, k=3, s=1, g=c2)
    def forward(self,x):
        if self.add:
           return x+self.cv2(self.cv1(x))
        else:
            return self.cv2(self.cv1(x))
class DCL(nn.Module):
    def __init__(self,c1,c2,n=1,shortcut=True):
        super().__init__()
        self.add=shortcut
        c_=int(c2*0.5)
        self.cv1=Conv(c1,c_,k=1,s=1)
        self.cv2=Conv(c1,c_,k=1,s=1)
        self.m1=nn.Sequential(*[Conv_Repli(c_, c_, k=3, s=1) for _ in range(n)])
        self.m2=nn.Sequential(*(FC(c_,c_,shortcut) for _ in range(n)))
        self.conv3=Conv(c2,c2,k=1,s=1)


    def forward(self,x):

        x1=self.cv1(x)
        x2=self.cv2(x)
        x3=torch.cat([self.m1(x1),self.m2(x2)],dim=1)

        x3=self.conv3(x3)
        return x3

class Patch(nn.Module):
    def __init__(self,c1,c2,k=2, s=2, p=None, g=1, act=True):
        super(Patch, self).__init__()
        self.patch=Conv(c1,c2, k, s, 0, g, act)

    def forward(self,x):

        return self.patch(x)


class LC(nn.Module):

    def __init__(self, c1, c2, shortcut=True, g=1):  # ch_in, ch_out, shortcut, groups
        super().__init__()

        self.cv1 = Conv(c1, c2, 3, 1, g=g)
        self.add=shortcut
        self.cv2=Conv(c1, c2) if shortcut and c1 != c2 else nn.Identity()
        pass

    def forward(self, x):
        return self.cv2(x) + self.cv1(x) if self.add else self.cv1(x)
#
class CSPl(nn.Module):
    # CSPl
    def __init__(self, c1, c2, n=1, shortcut=True, g=1, e=0.5):  # ch_in, ch_out, number, shortcut, groups, expansion
        super().__init__()
        c_ = int(c2 * e)  # hidden channels
        self.cv1 = Conv(c1, c_, 1, 1)
        self.cv2 = Conv(c1, c_, 1, 1)
        self.cv3 = Conv(2 * c_, c2, 1)
        self.m = nn.Sequential(*[LC(c_,c_,shortcut) for _ in range(n)])


    def forward(self, x):
        return self.cv3(torch.cat((self.m(self.cv1(x)), self.cv2(x)), dim=1))

class Bottlenecks(nn.Module):
    def __init__(self,c1,c2,n):
        super(Bottlenecks, self).__init__()
        self.m=nn.Sequential(*[Bottleneck(c1,c1,e=1) for _ in range(n)])
    def forward(self,x):
        return self.m(x)








class Contract(nn.Module):
    # Contract width-height into channels, i.e. x(1,64,80,80) to x(1,256,40,40)
    def __init__(self, gain=2):
        super().__init__()
        self.gain = gain

    def forward(self, x):
        b, c, h, w = x.size()  # assert (h / s == 0) and (W / s == 0), 'Indivisible gain'
        s = self.gain
        x = x.view(b, c, h // s, s, w // s, s)  # x(1,64,40,2,40,2)
        x = x.permute(0, 3, 5, 1, 2, 4).contiguous()  # x(1,2,2,64,40,40)
        return x.view(b, c * s * s, h // s, w // s)  # x(1,256,40,40)


class Expand(nn.Module):
    # Expand channels into width-height, i.e. x(1,64,80,80) to x(1,16,160,160)
    def __init__(self, gain=2):
        super().__init__()
        self.gain = gain

    def forward(self, x):
        b, c, h, w = x.size()  # assert C / s ** 2 == 0, 'Indivisible gain'
        s = self.gain
        x = x.view(b, s, s, c // s ** 2, h, w)  # x(1,2,2,16,80,80)
        x = x.permute(0, 3, 4, 1, 5, 2).contiguous()  # x(1,16,80,2,80,2)
        return x.view(b, c // s ** 2, h * s, w * s)  # x(1,16,160,160)


class Concat(nn.Module):
    # Concatenate a list of tensors along dimension
    def __init__(self, dimension=1):
        super().__init__()
        self.d = dimension

    def forward(self, x):
        return torch.cat(x, self.d)


class DetectMultiBackend(nn.Module):
    # YOLOv5 MultiBackend class for python inference on various backends
    def __init__(self, weights='yolov5s.pt', device=None, dnn=True):
        # Usage:
        #   PyTorch:      weights = *.pt
        #   TorchScript:            *.torchscript.pt
        #   CoreML:                 *.mlmodel
        #   TensorFlow:             *_saved_model
        #   TensorFlow:             *.pb
        #   TensorFlow Lite:        *.tflite
        #   ONNX Runtime:           *.onnx
        #   OpenCV DNN:             *.onnx with dnn=True
        #   TensorRT:               *.engine
        super().__init__()
        w = str(weights[0] if isinstance(weights, list) else weights)
        suffix, suffixes = Path(w).suffix.lower(), ['.pt', '.onnx', '.engine', '.tflite', '.pb', '', '.mlmodel']
        check_suffix(w, suffixes)  # check weights have acceptable suffix
        pt, onnx, engine, tflite, pb, saved_model, coreml = (suffix == x for x in suffixes)  # backend booleans
        jit = pt and 'torchscript' in w.lower()
        stride, names = 64, [f'class{i}' for i in range(1000)]  # assign defaults

        if jit:  # TorchScript
            LOGGER.info(f'Loading {w} for TorchScript inference...')
            extra_files = {'config.txt': ''}  # model metadata
            model = torch.jit.load(w, _extra_files=extra_files)
            if extra_files['config.txt']:
                d = json.loads(extra_files['config.txt'])  # extra_files dict
                stride, names = int(d['stride']), d['names']
        elif pt:  # PyTorch
            from models.experimental import attempt_load  # scoped to avoid circular import
            model = torch.jit.load(w) if 'torchscript' in w else attempt_load(weights, map_location=device)
            stride = int(model.stride.max())  # model stride
            names = model.module.names if hasattr(model, 'module') else model.names  # get class names
        elif coreml:  # CoreML *.mlmodel
            import coremltools as ct
            model = ct.models.MLModel(w)
        elif dnn:  # ONNX OpenCV DNN
            LOGGER.info(f'Loading {w} for ONNX OpenCV DNN inference...')
            check_requirements(('opencv-python>=4.5.4',))
            net = cv2.dnn.readNetFromONNX(w)
        elif onnx:  # ONNX Runtime
            LOGGER.info(f'Loading {w} for ONNX Runtime inference...')
            check_requirements(('onnx', 'onnxruntime-gpu' if torch.has_cuda else 'onnxruntime'))
            import onnxruntime
            session = onnxruntime.InferenceSession(w, None)
        elif engine:  # TensorRT
            LOGGER.info(f'Loading {w} for TensorRT inference...')
            import tensorrt as trt  # https://developer.nvidia.com/nvidia-tensorrt-download
            Binding = namedtuple('Binding', ('name', 'dtype', 'shape', 'data', 'ptr'))
            logger = trt.Logger(trt.Logger.INFO)
            with open(w, 'rb') as f, trt.Runtime(logger) as runtime:
                model = runtime.deserialize_cuda_engine(f.read())
            bindings = OrderedDict()
            for index in range(model.num_bindings):
                name = model.get_binding_name(index)
                dtype = trt.nptype(model.get_binding_dtype(index))
                shape = tuple(model.get_binding_shape(index))
                data = torch.from_numpy(np.empty(shape, dtype=np.dtype(dtype))).to(device)
                bindings[name] = Binding(name, dtype, shape, data, int(data.data_ptr()))
            binding_addrs = OrderedDict((n, d.ptr) for n, d in bindings.items())
            context = model.create_execution_context()
            batch_size = bindings['images'].shape[0]
        else:  # TensorFlow model (TFLite, pb, saved_model)
            if pb:  # https://www.tensorflow.org/guide/migrate#a_graphpb_or_graphpbtxt
                LOGGER.info(f'Loading {w} for TensorFlow *.pb inference...')
                import tensorflow as tf

                def wrap_frozen_graph(gd, inputs, outputs):
                    x = tf.compat.v1.wrap_function(lambda: tf.compat.v1.import_graph_def(gd, name=""), [])  # wrapped
                    return x.prune(tf.nest.map_structure(x.graph.as_graph_element, inputs),
                                   tf.nest.map_structure(x.graph.as_graph_element, outputs))

                graph_def = tf.Graph().as_graph_def()
                graph_def.ParseFromString(open(w, 'rb').read())
                frozen_func = wrap_frozen_graph(gd=graph_def, inputs="x:0", outputs="Identity:0")
            elif saved_model:
                LOGGER.info(f'Loading {w} for TensorFlow saved_model inference...')
                import tensorflow as tf
                model = tf.keras.models.load_model(w)
            elif tflite:  # https://www.tensorflow.org/lite/guide/python#install_tensorflow_lite_for_python
                if 'edgetpu' in w.lower():
                    LOGGER.info(f'Loading {w} for TensorFlow Lite Edge TPU inference...')
                    import tflite_runtime.interpreter as tfli
                    delegate = {'Linux': 'libedgetpu.so.1',  # install https://coral.ai/software/#edgetpu-runtime
                                'Darwin': 'libedgetpu.1.dylib',
                                'Windows': 'edgetpu.dll'}[platform.system()]
                    interpreter = tfli.Interpreter(model_path=w, experimental_delegates=[tfli.load_delegate(delegate)])
                else:
                    LOGGER.info(f'Loading {w} for TensorFlow Lite inference...')
                    import tensorflow as tf
                    interpreter = tf.lite.Interpreter(model_path=w)  # load TFLite model
                interpreter.allocate_tensors()  # allocate
                input_details = interpreter.get_input_details()  # inputs
                output_details = interpreter.get_output_details()  # outputs
        self.__dict__.update(locals())  # assign all variables to self

    def forward(self, im, augment=False, visualize=False, val=False):
        # YOLOv5 MultiBackend inference
        b, ch, h, w = im.shape  # batch, channel, height, width
        if self.pt:  # PyTorch
            y = self.model(im) if self.jit else self.model(im, augment=augment, visualize=visualize)
            return y if val else y[0]
        elif self.coreml:  # CoreML *.mlmodel
            im = im.permute(0, 2, 3, 1).cpu().numpy()  # torch BCHW to numpy BHWC shape(1,320,192,3)
            im = Image.fromarray((im[0] * 255).astype('uint8'))
            # im = im.resize((192, 320), Image.ANTIALIAS)
            y = self.model.predict({'image': im})  # coordinates are xywh normalized
            box = xywh2xyxy(y['coordinates'] * [[w, h, w, h]])  # xyxy pixels
            conf, cls = y['confidence'].max(1), y['confidence'].argmax(1).astype(np.float)
            y = np.concatenate((box, conf.reshape(-1, 1), cls.reshape(-1, 1)), 1)
        elif self.onnx:  # ONNX
            im = im.cpu().numpy()  # torch to numpy
            if self.dnn:  # ONNX OpenCV DNN
                self.net.setInput(im)
                y = self.net.forward()
            else:  # ONNX Runtime
                y = self.session.run([self.session.get_outputs()[0].name], {self.session.get_inputs()[0].name: im})[0]
        elif self.engine:  # TensorRT
            assert im.shape == self.bindings['images'].shape, (im.shape, self.bindings['images'].shape)
            self.binding_addrs['images'] = int(im.data_ptr())
            self.context.execute_v2(list(self.binding_addrs.values()))
            y = self.bindings['output'].data
        else:  # TensorFlow model (TFLite, pb, saved_model)
            im = im.permute(0, 2, 3, 1).cpu().numpy()  # torch BCHW to numpy BHWC shape(1,320,192,3)
            if self.pb:
                y = self.frozen_func(x=self.tf.constant(im)).numpy()
            elif self.saved_model:
                y = self.model(im, training=False).numpy()
            elif self.tflite:
                input, output = self.input_details[0], self.output_details[0]
                int8 = input['dtype'] == np.uint8  # is TFLite quantized uint8 model
                if int8:
                    scale, zero_point = input['quantization']
                    im = (im / scale + zero_point).astype(np.uint8)  # de-scale
                self.interpreter.set_tensor(input['index'], im)
                self.interpreter.invoke()
                y = self.interpreter.get_tensor(output['index'])
                if int8:
                    scale, zero_point = output['quantization']
                    y = (y.astype(np.float32) - zero_point) * scale  # re-scale
            y[..., 0] *= w  # x
            y[..., 1] *= h  # y
            y[..., 2] *= w  # w
            y[..., 3] *= h  # h
        y = torch.tensor(y) if isinstance(y, np.ndarray) else y
        return (y, []) if val else y

    def warmup(self, imgsz=(1, 3, 640, 640), half=False):
        # Warmup model by running inference once
        if self.pt or self.engine or self.onnx:  # warmup types
            if isinstance(self.device, torch.device) and self.device.type != 'cpu':  # only warmup GPU models
                im = torch.zeros(*imgsz).to(self.device).type(torch.half if half else torch.float)  # input image
                self.forward(im)  # warmup


class AutoShape(nn.Module):
    # YOLOv5 input-robust model wrapper for passing cv2/np/PIL/torch inputs. Includes preprocessing, inference and NMS
    conf = 0.25  # NMS confidence threshold
    iou = 0.45  # NMS IoU threshold
    classes = None  # (optional list) filter by class, i.e. = [0, 15, 16] for COCO persons, cats and dogs
    multi_label = False  # NMS multiple labels per box
    max_det = 1000  # maximum number of detections per image

    def __init__(self, model):
        super().__init__()
        LOGGER.info('Adding AutoShape... ')
        copy_attr(self, model, include=('yaml', 'nc', 'hyp', 'names', 'stride', 'abc'), exclude=())  # copy attributes
        self.model = model.eval()

    def _apply(self, fn):
        # Apply to(), cpu(), cuda(), half() to model tensors that are not parameters or registered buffers
        self = super()._apply(fn)
        m = self.model.model[-1]  # Detect()
        m.stride = fn(m.stride)
        m.grid = list(map(fn, m.grid))
        if isinstance(m.anchor_grid, list):
            m.anchor_grid = list(map(fn, m.anchor_grid))
        return self

    @torch.no_grad()
    def forward(self, imgs, size=640, augment=False, profile=False):
        # Inference from various sources. For height=640, width=1280, RGB images example inputs are:
        #   file:       imgs = 'data/images/zidane.jpg'  # str or PosixPath
        #   URI:             = 'https://ultralytics.com/images/zidane.jpg'
        #   OpenCV:          = cv2.imread('image.jpg')[:,:,::-1]  # HWC BGR to RGB x(640,1280,3)
        #   PIL:             = Image.open('image.jpg') or ImageGrab.grab()  # HWC x(640,1280,3)
        #   numpy:           = np.zeros((640,1280,3))  # HWC
        #   torch:           = torch.zeros(16,3,320,640)  # BCHW (scaled to size=640, 0-1 values)
        #   multiple:        = [Image.open('image1.jpg'), Image.open('image2.jpg'), ...]  # list of images

        t = [time_sync()]
        p = next(self.model.parameters())  # for device and type
        if isinstance(imgs, torch.Tensor):  # torch
            with amp.autocast(enabled=p.device.type != 'cpu'):
                return self.model(imgs.to(p.device).type_as(p), augment, profile)  # inference

        # Pre-process
        n, imgs = (len(imgs), imgs) if isinstance(imgs, list) else (1, [imgs])  # number of images, list of images
        shape0, shape1, files = [], [], []  # image and inference shapes, filenames
        for i, im in enumerate(imgs):
            f = f'image{i}'  # filename
            if isinstance(im, (str, Path)):  # filename or uri
                im, f = Image.open(requests.get(im, stream=True).raw if str(im).startswith('http') else im), im
                im = np.asarray(exif_transpose(im))
            elif isinstance(im, Image.Image):  # PIL Image
                im, f = np.asarray(exif_transpose(im)), getattr(im, 'filename', f) or f
            files.append(Path(f).with_suffix('.jpg').name)
            if im.shape[0] < 5:  # image in CHW
                im = im.transpose((1, 2, 0))  # reverse dataloader .transpose(2, 0, 1)
            im = im[..., :3] if im.ndim == 3 else np.tile(im[..., None], 3)  # enforce 3ch input
            s = im.shape[:2]  # HWC
            shape0.append(s)  # image shape
            g = (size / max(s))  # gain
            shape1.append([y * g for y in s])
            imgs[i] = im if im.data.contiguous else np.ascontiguousarray(im)  # update
        shape1 = [make_divisible(x, int(self.stride.max())) for x in np.stack(shape1, 0).max(0)]  # inference shape
        x = [letterbox(im, new_shape=shape1, auto=False)[0] for im in imgs]  # pad
        x = np.stack(x, 0) if n > 1 else x[0][None]  # stack
        x = np.ascontiguousarray(x.transpose((0, 3, 1, 2)))  # BHWC to BCHW
        x = torch.from_numpy(x).to(p.device).type_as(p) / 255  # uint8 to fp16/32
        t.append(time_sync())

        with amp.autocast(enabled=p.device.type != 'cpu'):
            # Inference
            y = self.model(x, augment, profile)[0]  # forward
            t.append(time_sync())

            # Post-process
            y = non_max_suppression(y, self.conf, iou_thres=self.iou, classes=self.classes,
                                    multi_label=self.multi_label, max_det=self.max_det)  # NMS
            for i in range(n):
                scale_coords(shape1, y[i][:, :4], shape0[i])

            t.append(time_sync())
            return Detections(imgs, y, files, t, self.names, x.shape)


class Detections:
    # YOLOv5 detections class for inference results
    def __init__(self, imgs, pred, files, times=None, names=None, shape=None):
        super().__init__()
        d = pred[0].device  # device
        gn = [torch.tensor([*(im.shape[i] for i in [1, 0, 1, 0]), 1, 1], device=d) for im in imgs]  # normalizations
        self.imgs = imgs  # list of images as numpy arrays
        self.pred = pred  # list of tensors pred[0] = (xyxy, conf, cls)
        self.names = names  # class names
        self.files = files  # image filenames
        self.xyxy = pred  # xyxy pixels
        self.xywh = [xyxy2xywh(x) for x in pred]  # xywh pixels
        self.xyxyn = [x / g for x, g in zip(self.xyxy, gn)]  # xyxy normalized
        self.xywhn = [x / g for x, g in zip(self.xywh, gn)]  # xywh normalized
        self.n = len(self.pred)  # number of images (batch size)
        self.t = tuple((times[i + 1] - times[i]) * 1000 / self.n for i in range(3))  # timestamps (ms)
        self.s = shape  # inference BCHW shape

    def display(self, pprint=False, show=False, save=False, crop=False, render=False, save_dir=Path('')):
        crops = []
        for i, (im, pred) in enumerate(zip(self.imgs, self.pred)):
            s = f'image {i + 1}/{len(self.pred)}: {im.shape[0]}x{im.shape[1]} '  # string
            if pred.shape[0]:
                for c in pred[:, -1].unique():
                    n = (pred[:, -1] == c).sum()  # detections per class
                    s += f"{n} {self.names[int(c)]}{'s' * (n > 1)}, "  # add to string
                if show or save or render or crop:
                    annotator = Annotator(im, example=str(self.names))
                    for *box, conf, cls in reversed(pred):  # xyxy, confidence, class
                        label = f'{self.names[int(cls)]} {conf:.2f}'
                        if crop:
                            file = save_dir / 'crops' / self.names[int(cls)] / self.files[i] if save else None
                            crops.append({'box': box, 'conf': conf, 'cls': cls, 'label': label,
                                          'im': save_one_box(box, im, file=file, save=save)})
                        else:  # all others
                            annotator.box_label(box, label, color=colors(cls))
                    im = annotator.im
            else:
                s += '(no detections)'

            im = Image.fromarray(im.astype(np.uint8)) if isinstance(im, np.ndarray) else im  # from np
            if pprint:
                LOGGER.info(s.rstrip(', '))
            if show:
                im.show(self.files[i])  # show
            if save:
                f = self.files[i]
                im.save(save_dir / f)  # save
                if i == self.n - 1:
                    LOGGER.info(f"Saved {self.n} image{'s' * (self.n > 1)} to {colorstr('bold', save_dir)}")
            if render:
                self.imgs[i] = np.asarray(im)
        if crop:
            if save:
                LOGGER.info(f'Saved results to {save_dir}\n')
            return crops

    def print(self):
        self.display(pprint=True)  # print results
        LOGGER.info(f'Speed: %.1fms pre-process, %.1fms inference, %.1fms NMS per image at shape {tuple(self.s)}' %
                    self.t)

    def show(self):
        self.display(show=True)  # show results

    def save(self, save_dir='runs/detect/exp'):
        save_dir = increment_path(save_dir, exist_ok=save_dir != 'runs/detect/exp', mkdir=True)  # increment save_dir
        self.display(save=True, save_dir=save_dir)  # save results

    def crop(self, save=True, save_dir='runs/detect/exp'):
        save_dir = increment_path(save_dir, exist_ok=save_dir != 'runs/detect/exp', mkdir=True) if save else None
        return self.display(crop=True, save=save, save_dir=save_dir)  # crop results

    def render(self):
        self.display(render=True)  # render results
        return self.imgs

    def pandas(self):
        # return detections as pandas DataFrames, i.e. print(results.pandas().xyxy[0])
        new = copy(self)  # return copy
        ca = 'xmin', 'ymin', 'xmax', 'ymax', 'confidence', 'class', 'name'  # xyxy columns
        cb = 'xcenter', 'ycenter', 'width', 'height', 'confidence', 'class', 'name'  # xywh columns
        for k, c in zip(['xyxy', 'xyxyn', 'xywh', 'xywhn'], [ca, ca, cb, cb]):
            a = [[x[:5] + [int(x[5]), self.names[int(x[5])]] for x in x.tolist()] for x in getattr(self, k)]  # update
            setattr(new, k, [pd.DataFrame(x, columns=c) for x in a])
        return new

    def tolist(self):
        # return a list of Detections objects, i.e. 'for result in results.tolist():'
        x = [Detections([self.imgs[i]], [self.pred[i]], self.names, self.s) for i in range(self.n)]
        for d in x:
            for k in ['imgs', 'pred', 'xyxy', 'xyxyn', 'xywh', 'xywhn']:
                setattr(d, k, getattr(d, k)[0])  # pop out of list
        return x

    def __len__(self):
        return self.n


class Classify(nn.Module):
    # Classification head, i.e. x(b,c1,20,20) to x(b,c2)
    def __init__(self, c1, c2, k=1, s=1, p=None, g=1):  # ch_in, ch_out, kernel, stride, padding, groups
        super().__init__()
        self.aap = nn.AdaptiveAvgPool2d(1)  # to x(b,c1,1,1)
        self.conv = nn.Conv2d(c1, c2, k, s, autopad(k, p), groups=g)  # to x(b,c2,1,1)
        self.flat = nn.Flatten()

    def forward(self, x):
        z = torch.cat([self.aap(y) for y in (x if isinstance(x, list) else [x])], 1)  # cat if list
        return self.flat(self.conv(z))  # flatten to x(b,c2)



# class CSPl(nn.Module):
#     # CSPl
#     def __init__(self, c1, c2, shortcut=True):  # ch_in, ch_out, number, shortcut, groups, expansion
#         super().__init__()
#         c_ = int(c2* 0.5)
#         c_2 = int(c2- c_)
#         self.cv1 = Conv(c1, c2, 1, 1)
#         self.cv2 = Conv(c_2, c_2, 1, 1)
#         self.cv3=Conv(c_, c_, 1)
#         self.cv4 = Conv(c_+c_2, c2, 1)
#         self.m = nn.Sequential(*[LC(c_,c_,shortcut) for _ in range(1)])
#         self.a=1
#
#
#
#     def forward(self, x):
#         x=self.cv1(x)
#         x1,x2=x.chunk(2,dim=1)
#         return self.cv4(torch.cat((self.m(self.cv3(x2)),self.cv2(x1)),dim=1))


# class CSPl(nn.Module):
#     # CSPl
#     def __init__(self, c1, c2, n=1, shortcut=True, g=1, e=0.5):  # ch_in, ch_out, number, shortcut, groups, expansion
#         super().__init__()
#         c_ = int(c2 * e)  # hidden channels
#         self.cv1 = Conv(c1, c_, 3, 1)
#         # self.cv2 = Conv(c_, c_, 1, 1)
#         self.cv3=Conv(c_, c_, 1, 1)
#         self.cv4 = Conv(2 * c_, c2, 1)
#         self.m = nn.Sequential(*[LC(c_,c_,shortcut) for _ in range(n)])
#
#
#     def forward(self, x):
#         x=self.cv1(x)
#         return self.cv4(torch.cat((self.m(self.cv3(x)), x), dim=1))


# class CSPl(nn.Module):
#     # CSPl
#     def __init__(self, c1, c2, n=1, shortcut=True, g=1, e=0.5):  # ch_in, ch_out, number, shortcut, groups, expansion
#         super().__init__()
#         c_ = int(c2*e)  # hidden channels
#         self.cv1 = Conv(c1, c_, 1, 1)
#         self.cv2 = Conv(c_, c_, 1, 1)
#         self.cv3= Conv(c_, c_, 1, 1)
#         self.cv4 = Conv(2 * c_, c2, 1)
#         self.m = nn.Sequential(*[LC(c_,c_,shortcut) for _ in range(n)])
#
#
#     def forward(self, x):
#         x=self.cv1(x)
#         return self.cv4(torch.cat((self.m(self.cv3(x)), self.cv2(x)), dim=1))





class NEMF(nn.Module):

    def __init__(self,width,height, channel):
        super().__init__()
        print(width,height,channel)
        self.act=nn.Sigmoid()
        self.width=width
        self.height=height
        self.up=nn.Upsample(scale_factor=2)
        self.register_buffer('pre_computed_dct_weights', get_dct_weights( self.width, self.height, channel))

    def forward(self,x):
        x1,x2=x
        if x1.shape[2]==self.width:
            x2=self.act(torch.sum(self.up(x2)*self.pre_computed_dct_weights,dim=1,keepdim=True))
        else:
            x2 = self.act(self.up(x2).mean(dim=1, keepdim=True))


        x=x1-x1*x2

        return x

def get_1d_dct(i, freq, L):
    result = math.cos(math.pi * freq * (i + 0.5) / L) / math.sqrt(L)
    if freq == 0:
        return result
    else:
        return result * math.sqrt(2)

def get_dct_weights( width, height, channel, fidx_u= [0,0,6,0,0,1,1,4,5,1,3,0,0,0,2,3]):

    scale_ratio = channel//7
    fidx_u = [u*scale_ratio for u in fidx_u]

    dct_weights = torch.zeros(1, channel, width*height)
    c_part = width*height // len(fidx_u)
    # split channel for multi-spectal attention
    for i,u_x in enumerate(fidx_u):
        for t in range(channel):
                dct_weights[:, t, i * c_part: (i+1)*c_part]\
                =get_1d_dct(t, u_x, channel)

    # Eq. 7 in our paper
    return dct_weights.view(1, channel, width,height)


if __name__ == '__main__':
    import thop
    x=torch.rand([3,64,100,100])
    model=Conv(64,64,k=6,s=4,p=2)
    y=model(x)
    model = Conv_Re(64, 64, k=3, s=1)
    Flops, parms=thop.profile(model,inputs=(x,),verbose=False)
    print(Flops)
    print(model(x).shape)