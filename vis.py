import numpy
import torchvision.utils as vutils
import torch
import matplotlib.pyplot as plt
import math
import os
import matplotlib as mpl
import numpy as np
os.environ['KMP_DUPLICATE_LIB_OK']='True'

def show_kernal(model,Save_dir,filter=False):
    n1 = 512

    for name, param in model.named_parameters():
        if 'conv' in name and 'weight' in name :
            try:
                in_channels = param.size()[1]
            except:
                continue
            out_channels = param.size()[0]  # 输出通道，表示卷积核的个数

            k_w, k_h = param.size()[3], param.size()[2]  # 卷积核的尺寸
            if k_w==1:
                continue
            n=min(out_channels,n1)

            if not filter:
                param=torch.mean(param,dim=1).mean(dim=0).cpu().float()
                plt.imshow(param,'Oranges',interpolation="bilinear")
                plt.colorbar()
                plt.axis('off')

            else:
                param = torch.mean(param, dim=1).cpu().float()
                fig, ax = plt.subplots(math.ceil(n / 8), 8, tight_layout=False)
                ax = ax.ravel()
                for i in range(n):
                    ax[i].imshow(param[i].float(),'Oranges',aspect='auto',interpolation="bilinear")
                    ax[i].axis('off')


                plt.subplots_adjust(left=None, bottom=None, right=None, top=None, wspace=None, hspace=None)

            plt.subplots_adjust(wspace=0, hspace=0)
            save_dir=f'{name}_all.png'
            print(f'Saving {save_dir}... ({n}/{out_channels})')

            plt.savefig(Save_dir+save_dir, dpi=300, bbox_inches='tight')
            plt.close()
            pass
            # writer.add_image(f'{name}_all', kernel_grid, global_step=0)
save_dir="vis_conv/"

path="weights/yolov5s.pt"
name=os.path.splitext(path.split('/')[-1])[0]
save_dir+=name+'/'
model=torch.load(path)
if not os.path.exists(save_dir):
    os.makedirs(save_dir)
show_kernal(model["model"],save_dir,False)


