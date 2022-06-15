#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @python: 3.6

import torch
from torch import nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, RandomSampler
import sys
import os


def test_img(net_g, datatest, args):
    net_g.eval()
    # testing
    test_loss = 0
    correct = 0
    if args.test_size:
        sampler = RandomSampler(datatest, num_samples=args.test_size)
        data_loader = DataLoader(datatest, sampler=sampler, batch_size=args.bs)
        test_size = args.test_size
    else:
        data_loader = DataLoader(datatest, batch_size=args.bs)
        test_size = len(data_loader.dataset)
    # l = len(data_loader)
    for idx, (data, target) in enumerate(data_loader):
        if args.gpu != -1:
            data, target = data.cuda(), target.cuda()
        log_probs = net_g(data)
        # sum up batch loss
        test_loss += F.cross_entropy(log_probs, target, reduction='sum').item()
        # get the index of the max log-probability
        y_pred = log_probs.data.max(1, keepdim=True)[1]
        correct += y_pred.eq(target.data.view_as(y_pred)).long().cpu().sum()

    test_loss /= test_size
    accuracy = 100.00 * correct / test_size
    if args.verbose:
        print('\nTest set: Average loss: {:.4f} \nAccuracy: {}/{} ({:.2f}%)\n'.format(
            test_loss, correct, test_size, accuracy))
    return accuracy.item(), test_loss

def comp_activity(net_g, dataset, args):
    net_g.eval()
    # testing
    data_loader = DataLoader(dataset, batch_size=args.bs)
    l = len(data_loader)
    for idx, (data, target) in enumerate(data_loader):
        if args.gpu != -1:
            data, target = data.cuda(), target.cuda()
        activity = torch.zeros(net_g(data, count_active_layers = True))
        break
    batch_count = 0
    for idx, (data, target) in enumerate(data_loader):
        if args.gpu != -1:
            data, target = data.cuda(), target.cuda()
        activity += torch.tensor(net_g(data, report_activity = True))
        # sum up batch loss
        batch_count += 1
    activity = activity/batch_count

    return activity