import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
from typing import Tuple, Dict, Any, Optional
from einops import rearrange

class Patchify(nn.Module):
    """Convert a set of images or a movie into patch vectors"""
    def __init__(self,
                 patch_size=(16,16),
                 temporal_dim=1,
                 squeeze_channel_dim=True
    ):
        super().__init__()
        self.set_patch_size(patch_size)
        self.temporal_dim = temporal_dim
        assert self.temporal_dim in [1,2], self.temporal_dim
        self._squeeze_channel_dim = squeeze_channel_dim

    @property
    def num_patches(self):
        if (self.T is None) or (self.H is None) or (self.W is None):
            return None
        else:
            return (self.T // self.pt) * (self.H // self.ph) * (self.W // self.pw)
        
    def set_patch_size(self, patch_size):
        self.patch_size = patch_size
        if len(self.patch_size) == 2:
            self.ph, self.pw = self.patch_size
            self.pt = 1
            self._patches_are_3d = False
        elif len(self.patch_size) == 3:
            self.pt, self.ph, self.pw = self.patch_size
            self._patches_are_3d = True
        else:
            raise ValueError("patch_size must be a 2- or 3-tuple, but is %s" % self.patch_size)

        self.shape_inp = self.rank_inp = self.H = self.W = self.T = None
        self.D = self.C = self.E = self.embed_dim = None

    def _check_shape(self, x):
        self.shape_inp = x.shape
        self.rank_inp = len(self.shape_inp)
        self.H, self.W = self.shape_inp[-2:]
        assert (self.H % self.ph) == 0 and (self.W % self.pw) == 0, (self.shape_inp, self.patch_size)
        if (self.rank_inp == 5) and self._patches_are_3d:
            self.T = self.shape_inp[self.temporal_dim]
            assert (self.T % self.pt) == 0, (self.T, self.pt)
        elif self.rank_inp == 5:
            self.T = self.shape_inp[self.temporal_dim]
        else:
            self.T = 1

    def split_by_time(self, x):
        shape = x.shape
        assert shape[1] % self.T == 0, (shape, self.T)
        return x.view(shape[0], self.T, shape[1] // self.T, *shape[2:])

    def merge_by_time(self, x):
        shape = x.shape
        return x.view(shape[0], shape[1]*shape[2], *shape[3:])

    def video_to_patches(self, x):
        if self.rank_inp == 4:
            assert self.pt == 1, (self.pt, x.shape)
            x = rearrange(x, 'b c (h ph) (w pw) -> b (h w) (ph pw) c', ph=self.ph, pw=self.pw)
        else:
            assert self.rank_inp == 5, (x.shape, self.rank_inp, self.shape_inp)
            dim_order = 'b (t pt) c (h ph) (w pw)' if self.temporal_dim == 1 else 'b c (t pt) (h ph) (w pw)'
            x = rearrange(x, dim_order + ' -> b (t h w) (pt ph pw) c', pt=self.pt, ph=self.ph, pw=self.pw)

        self.N, self.D, self.C = x.shape[-3:]
        self.embed_dim = self.E = self.D * self.C
        return x

    def patches_to_video(self, x, mask_mode='zeros'):
        shape = x.shape
        rank = len(shape)
        if rank == 4:
            B,_N,_D,_C = shape
        else:
            assert rank == 3, rank
            B,_N,_E = shape
            assert (_E % self.D == 0), (_E, self.D)
            x = x.view(B,_N,self.D,-1)

        if _N < self.num_patches:
            masked_patches = self.get_masked_patches(
                x,
                num_patches=(self.num_patches - _N),
                mask_mode=mask_mode)
            x = torch.cat([x, masked_patches], 1)

        x = rearrange(
            x,
            'b (t h w) (pt ph pw) c -> b c (t pt) (h ph) (w pw)',
            pt=self.pt, ph=self.ph, pw=self.pw,
            t=(self.T // self.pt), h=(self.H // self.ph), w=(self.W // self.pw))

        if self.rank_inp == 5 and (self.temporal_dim == 1):
            x = x.transpose(1, 2)
        elif self.rank_inp == 4:
            assert x.shape[2] == 1, x.shape
            x = x[:,:,0]
        return x

    @staticmethod
    def get_masked_patches(x, num_patches, mask_mode='zeros'):
        shape = x.shape
        patches_shape = (shape[0], num_patches, *shape[2:])
        if mask_mode == 'zeros':
            return torch.zeros(patches_shape).to(x.device).to(x.dtype).detach()
        elif mask_mode == 'gray':
            return 0.5 * torch.ones(patches_shape).to(x.device).to(x.dtype).detach()
        else:
            raise NotImplementedError("Haven't implemented mask_mode == %s" % mask_mode)

    def forward(self, x, to_video=False, mask_mode='zeros'):
        if not to_video:
            self._check_shape(x)
            x = self.video_to_patches(x)
            return x if not self._squeeze_channel_dim else x.view(x.size(0), self.N, -1)

        else: # x are patches
            assert (self.shape_inp is not None) and (self.num_patches is not None)
            x = self.patches_to_video(x, mask_mode=mask_mode)
            return x


class PositionalEncoding(nn.Module):
    def __init__(self, d_model, max_len=5000):
        super().__init__()
        position = torch.arange(max_len).unsqueeze(1)
        div_term = torch.exp(torch.arange(0, d_model, 2) * (-torch.log(torch.tensor(10000.0)) / d_model))
        pe = torch.zeros(1, max_len, d_model)
        pe[0, :, 0::2] = torch.sin(position * div_term)
        pe[0, :, 1::2] = torch.cos(position * div_term)
        self.register_buffer('pe', pe)

    def forward(self, x):
        return x + self.pe[:, :x.size(1)]


