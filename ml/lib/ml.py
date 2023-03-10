"""

ml.py:  Classes for introducing a second set label to a dataset; computing the
        DeCorr, HalfCorr, and DeCov loss functions; and creating one-target and
        two-target multilayer perceptrons.

"""

__author__ = "Louis Kang"
__date__ = "2023/03/07"
__license__ = "GPLv3"
__reference__ = "To be determined"



import os, sys

import numpy as np
import torch
from torch import nn
from torch.utils.data import Dataset


# Taking a subset of a dataset and adding an additional label to each item
class RelabeledSubset(Dataset):
    def __init__(self, dataset, class_size='all', class_inds='all',
                 target2_config='none',
                 transform=None, target_transform=None):
        self.class_inds = class_inds
        self.class_size = class_size
        
        if class_inds == 'all' and class_size == 'all':
            self.data = torch.as_tensor(dataset.data)
            self.targets = torch.as_tensor(dataset.targets)
        
        else:
            if class_inds == 'all':
                class_inds = list(dataset.class_to_idx.values())
            # If class_inds are provided, take only those classes
            inds_by_class = [torch.where(dataset.targets == class_idx)[0]
                             for class_idx in class_inds]
            # Pick subsample of images in each valid class
            if class_size != 'all':
                inds_by_class = [inds[:class_size] for inds in inds_by_class]
                
            # Generate subset
            subset_inds = torch.cat(inds_by_class)
            subset_inds = subset_inds[torch.randperm(len(subset_inds))]
            self.data = torch.index_select(torch.as_tensor(dataset.data),
                                           0, subset_inds)
            self.targets = torch.index_select(torch.as_tensor(dataset.targets),
                                              0, subset_inds)
        
        # Each item gets a different second label
        if target2_config == 'index':
            self.targets2 = torch.arange(len(self.targets),
                                         device=self.targets.device)
        # Second labels are obtained by shuffling the original labels
        elif target2_config == 'shuffle':
            perm = torch.randperm(len(self.targets))
            self.targets2 = self.targets[perm].clone()
        # An integer argument determines the number of randomly assigned sets
        elif type(target2_config) == int:
            self.targets2 = torch.arange(len(self.targets),
                                         device=self.targets.device)
            perm = torch.randperm(len(self.targets))
            self.targets2 = (self.targets2 % target2_config)[perm]
        elif target2_config == 'none':
            self.targets2 = None
        else:
            raise Exception("target2_config must be 'index',"
                            " 'shuffle', 'none', or an integer")
        
        self.transform = transform
        self.target_transform = target_transform

    def __len__(self):
        return len(self.targets)
        
    def __getitem__(self, idx):
        img = self.data[idx].numpy()
        target = int(self.targets[idx])
        
        if self.transform:
            img = self.transform(img)
        if self.target_transform:
            target = self.target_transform(target)
        
        if self.targets2 is None:
            return img, target
        
        else:
            target2 = int(self.targets2[idx])
            if self.target_transform:
                target2 = self.target_transform(target2)
            return img, target, target2



# Covariance matrix
def cov_matrix(X):
    if X.ndim < 2:
        raise Exception("input must have at least 2 dimensions")
    N = X.shape[-1]
    mean = torch.mean(X, dim=-1, keepdim=True)
    X = X - mean
    return 1./(N-1) * X @ X.transpose(-1, -2)

# DeCov loss by Cogswell et al., ICLR (2016)
def decov_criterion(activations):
    cov = cov_matrix(activations.T)
    cov.diagonal().zero_()
    decov_loss = cov.square().sum()
    
    return decov_loss


# Square of correlation matrix with eps introduced in the denominator to aid
# numerical convergence
def corr_matrix_sq(X, eps=0.):
    if X.ndim < 2:
        raise Exception("input must have at least 2 dimensions")
    N = X.shape[-1]
    mean = torch.mean(X, dim=-1, keepdim=True)
    X = X - mean
    var = torch.var(X, dim=-1) + eps
    corr_sq = X @ X.transpose(-1, -2)
    corr_sq = corr_sq.square()
    corr_sq /= torch.outer(var, var)
    return 1./(N-1.)**2 * corr_sq

# DeCorr loss function
def decorr_criterion(activations, eps=1e-3):
    corr_sq = corr_matrix_sq(activations, eps)
    corr_sq.diagonal().zero_()
    decorr_loss = corr_sq.sum()
    
    return decorr_loss

# HalfCorr loss function
def halfcorr_criterion(activations, eps=1e-3):
    half = int(round(activations.shape[-1]/2))
    corr_sq = corr_matrix_sq(activations[:,half:], eps)
    corr_sq.diagonal().zero_()
    halfcorr_loss = corr_sq.sum()
    
    return halfcorr_loss


# Two-layer perceptron for MNIST data and a single task
class MLP(nn.Module):
    def __init__(self, hidden_size, target_size,
                 nonlinearity1=nn.ReLU(inplace=True), nonlinearity2=nn.Identity()):
        super().__init__()
        self.hidden_size = hidden_size
        self.target_size = target_size
        self.nonlinearity1 = nonlinearity1
        self.nonlinearity2 = nonlinearity2
        
        self.linear_block1 = nn.Sequential(
            nn.Flatten(),
            nn.Linear(28 * 28, hidden_size),
            self.nonlinearity1,
        )
        self.linear_block2 = nn.Sequential(
            nn.Linear(hidden_size, hidden_size),
            self.nonlinearity2,
        )
        self.classifier = nn.Linear(hidden_size, target_size)

    def forward(self, x):
        x = self.linear_block1(x)
        activations = self.linear_block2(x)
        logits = self.classifier(activations)
        return activations, logits
    
    
# Two-layer perceptron with MNIST data and two tasks
class TwoTargetMLP(nn.Module):
    def __init__(self, hidden_size, target1_size, target2_size,
                 nonlinearity1=nn.ReLU(inplace=True), nonlinearity2=nn.Identity()):
        super().__init__()
        self.hidden_size = hidden_size
        self.target1_size = target1_size
        self.target2_size = target2_size
        self.nonlinearity1 = nonlinearity1
        self.nonlinearity2 = nonlinearity2
        
        self.linear_block1 = nn.Sequential(
            nn.Flatten(),
            nn.Linear(28 * 28, hidden_size),
            self.nonlinearity1,
        )
        self.linear_block2 = nn.Sequential(
            nn.Linear(hidden_size, hidden_size),
            self.nonlinearity2,
        )
        self.classifier1 = nn.Linear(hidden_size, target1_size)
        self.classifier2 = nn.Linear(hidden_size, target2_size)

    def forward(self, x):
        x = self.linear_block1(x)
        activations = self.linear_block2(x)
        logits1 = self.classifier1(activations)
        logits2 = self.classifier2(activations)
        return activations, logits1, logits2
