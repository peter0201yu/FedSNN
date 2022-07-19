#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Python version: 3.6

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import copy
import numpy as np
import pandas as pd
from pathlib import Path
from torchvision import datasets, transforms
import torch
import torch.nn as nn

from utils.sampling import mnist_iid, mnist_non_iid, cifar_iid, cifar_non_iid, mnist_dvs_iid, mnist_dvs_non_iid, nmnist_iid, nmnist_non_iid
from utils.options import args_parser
from torch.utils.data import DataLoader, Dataset, RandomSampler
from models.test import test_img
import models.vgg as ann_models
import models.resnet as resnet_models
import models.vgg_spiking_bntt as snn_models_bntt
from models.simple_conv_cf10 import Simple_CF10_BNTT, VGG5_CF10_NoBNTT
from models.simple_conv_mnist import Simple_Mnist_BNTT, Simple_Mnist_NoBNTT
import models.client_selection as client_selection
from RatevsDirect.util import accuracy, AverageMeter, adjust_learning_rate

import tables
import yaml
import glob
import json

from PIL import Image

from pysnn.datasets import nmnist_train_test

class DatasetSplit(Dataset):
    def __init__(self, dataset, idxs):
        self.dataset = dataset
        self.idxs = list(idxs)

    def __len__(self):
        return len(self.idxs)

    def __getitem__(self, item):
        image, label = self.dataset[self.idxs[item]]
        return image, label

if __name__ == '__main__':
    # parse args
    args = args_parser()
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    args.device = torch.device('cuda:{}'.format(args.gpu) if torch.cuda.is_available() and args.gpu != -1 else 'cpu')
    if args.device != 'cpu':
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False
    # torch.set_default_tensor_type('torch.cuda.FloatTensor')

    dataset_keys = None
    h5fs = None
    # load dataset and split users
    if args.dataset == 'CIFAR10':
        trans_cifar = transforms.Compose([transforms.ToTensor(), transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))])
        dataset_train = datasets.CIFAR10('../data/cifar', train=True, download=True, transform=trans_cifar)
        dataset_test = datasets.CIFAR10('../data/cifar', train=False, download=True, transform=trans_cifar)
        # if args.iid:
        #     dict_users = cifar_iid(dataset_train, args.num_users)
        # else:
        #     dict_users = cifar_non_iid(dataset_train, args.num_classes, args.num_users)
    elif args.dataset == 'CIFAR100':
        trans_cifar = transforms.Compose([transforms.ToTensor(), transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))])
        dataset_train = datasets.CIFAR100('../data/cifar100', train=True, download=True, transform=trans_cifar)
        dataset_test = datasets.CIFAR100('../data/cifar100', train=False, download=True, transform=trans_cifar)
        if args.iid:
            dict_users = cifar_iid(dataset_train, args.num_users)
        else:
            dict_users = cifar_non_iid(dataset_train, args.num_classes, args.num_users)
    elif args.dataset == 'N-MNIST':
        dataset_train, dataset_test = nmnist_train_test("nmnist/data")
        if args.iid:
            dict_users = nmnist_iid(dataset_train, args.num_users)
        else:
            dict_users = nmnist_non_iid(dataset_train, args.num_classes, args.num_users)
    elif args.dataset == 'MNIST':
        trans_mnist = transforms.Compose([transforms.ToTensor(), transforms.Normalize((0.1307,), (0.3081,))])
        dataset_train = datasets.MNIST('../data/mnist', train=True, download=True, transform=trans_mnist)
        dataset_test = datasets.MNIST('../data/mnist', train=False, download=True, transform=trans_mnist)
        if args.iid:
            dict_users = mnist_iid(dataset_train, args.num_users)
        else:
            dict_users = mnist_non_iid(dataset_train, args.num_classes, args.num_users)
    elif args.dataset == 'EMNIST':
        # same transform and splitting as MNIST
        trans_mnist = transforms.Compose([transforms.ToTensor(), transforms.Normalize((0.1307,), (0.3081,))])
        dataset_train = datasets.EMNIST('../data/emnist', 'bymerge', train=True, download=True, transform=trans_mnist)
        dataset_test = datasets.EMNIST('../data/emnist', 'bymerge', train=False, download=True, transform=trans_mnist)
        if args.iid:
            dict_users = mnist_iid(dataset_train, args.num_users)
        else:
            dict_users = mnist_non_iid(dataset_train, args.num_classes, args.num_users)
    else:
        exit('Error: unrecognized dataset')
    # img_size = dataset_train[0][0].shape

    # build model
    max_timestep = 25
    model_args = {'args': args}
    if args.model[0:3].lower() == 'vgg':
        if args.snn:
            model_args = {'num_cls': args.num_classes, 'timesteps': max_timestep}
            net = snn_models_bntt.SNN_VGG9_BNTT(**model_args).cuda()
        else:
            model_args = {'vgg_name': args.model, 'labels': args.num_classes, 'dataset': args.dataset, 'kernel_size': 3, 'dropout': args.dropout}
            net = ann_models.VGG(**model_args).cuda()
    elif args.model[0:6].lower() == 'resnet':
        if args.snn:
            pass
        else:
            model_args = {'num_cls': args.num_classes}
            net = resnet_models.Network(**model_args).cuda()
    elif args.model == 'simple':
        model_args = {'num_cls': args.num_classes, 'timesteps': args.timesteps, 'img_size': args.img_size}
        if args.dataset == 'MNIST' or args.dataset == 'EMNIST':
            if args.bntt:
                model_args['leak_mem'] = 0.5
                net = Simple_Mnist_BNTT(**model_args).cuda()
            else:
                model_args['leak_mem'] = 0.5
                net = Simple_Mnist_NoBNTT(**model_args).cuda()
        else:
            if args.bntt:
                model_args['leak_mem'] = 0.5
                net = Simple_CF10_BNTT(**model_args).cuda()
            else:
                model_args['leak_mem'] = 0.5
                net = VGG5_CF10_NoBNTT(**model_args).cuda()
    else:
        exit('Error: unrecognized model')
    # print(net)

    # evaluate random model
    # net = torch.nn.DataParallel(net)
    # net.eval()
    # acc_train, loss_train = test_img(net, dataset_test, args)
    # print("Random Model Test accuracy: {:.2f}".format(acc_train))

    # Print the SNN model, optimizer, and simulation parameters
    # print("********** SNN simulation parameters **********")
    # print("Simulation # time-step : {}".format(args.timesteps))
    # print("Membrane decay rate : {0:.2f}\n".format(model_args["leak_mem"]))
    print("********** SNN learning parameters **********")
    print("Backprop optimizer     : {}".format(args.optimizer))
    print("Number of epochs       : {}".format(args.epochs))
    print("Learning rate          : {}".format(args.lr))


    # training
    loss_train_list = []

    # metrics to store
    ms_acc_train_list, ms_loss_train_list = [], []
    ms_acc_test_list, ms_loss_test_list = [], []

    # print("len(dict_users[0]): ", len(dict_users[0]))

    loss_func = nn.CrossEntropyLoss()
    # ldr_train = DataLoader(DatasetSplit(dataset=dataset_train, idxs=dict_users[0]), batch_size=args.local_bs, shuffle=True, drop_last=True)
    num_samples = len(dataset_train) // 100
    sampler = RandomSampler(dataset_train, num_samples=num_samples)
    ldr_train = DataLoader(dataset_train, sampler=sampler, batch_size=args.local_bs, drop_last=True)
    # print("Data: ", len(ldr_train.dataset))

    # train and update
    if args.optimizer == "SGD":
        optimizer = torch.optim.SGD(net.parameters(), lr=args.lr, momentum=0.9, weight_decay = 1e-4)
    elif args.optimizer == "Adam":
        optimizer = torch.optim.Adam(net.parameters(), lr = args.lr)
    else:
        print("Invalid optimizer")

    if args.timestep_pattern == "uniform":
        timesteps_list = [args.timesteps] * 60
    elif args.timestep_pattern == "increasing":
        timesteps_list = [15] * 20 + [20] * 20 + [25] * 20
    elif args.timestep_pattern == "decreasing":
        timesteps_list = [25] * 20 + [20] * 20 + [15] * 20
    else:
        timesteps_list = [25] * 60

    for epoch in range(args.epochs):
        print("ROUND ", epoch)
        net.set_timestep(timesteps_list[epoch])
        net.train()
        
        epoch_loss = []
        trained_data_size = 0
        for local_ep in range(args.local_ep):
            print("local epoch: ", local_ep)
            batch_loss = []
            for batch_idx, (images, labels) in enumerate(ldr_train):
                images, labels = images.to(args.device), labels.to(args.device)
                trained_data_size += len(images)
                net.zero_grad()
                log_probs = net(images)
                loss = loss_func(log_probs, labels)
                loss.backward()
                optimizer.step()
                if args.verbose and batch_idx % 10 == 0:
                    print('Update Epoch: {} [{}/{} ({:.0f}%)]\tLoss: {:.6f}'.format(
                        local_ep, batch_idx * len(images), num_samples,
                               100. * batch_idx / len(ldr_train), loss.item()))
                batch_loss.append(loss.item())
            epoch_loss.append(sum(batch_loss)/len(batch_loss))

        # avg of all local epochs (In federated learning, only keeps track of FedLearn epochs)
        loss_train_list.append(sum(epoch_loss) / len(epoch_loss))
    
        if epoch % args.eval_every == 0:
            # testing
            net.eval()
            acc_train, loss_train = test_img(net, dataset_train, args)
            print("Round {:d}, Training accuracy: {:.2f}".format(epoch, acc_train))
            acc_test, loss_test = test_img(net, dataset_test, args)
            print("Round {:d}, Testing accuracy: {:.2f}".format(epoch, acc_test))
    
            # Add metrics to store
            ms_acc_train_list.append(acc_train)
            ms_acc_test_list.append(acc_test)
            ms_loss_train_list.append(loss_train)
            ms_loss_test_list.append(loss_test)

    Path('./{}'.format(args.result_dir)).mkdir(parents=True, exist_ok=True)
    # plot loss curve
    plt.figure()
    plt.plot(range(len(loss_train_list)), loss_train_list)
    plt.ylabel('train_loss')
    plt.savefig('./{}/fed_loss_{}_{}_{}_C{}_iid{}.png'.format(args.result_dir,args.dataset, args.model, args.epochs, args.frac, args.iid))

    # testing
    net.eval()
    acc_train, loss_train = test_img(net, dataset_train, args)
    print("Final Training accuracy: {:.2f}".format(acc_train))
    acc_test, loss_test = test_img(net, dataset_test, args)
    print("Final Testing accuracy: {:.2f}".format(acc_test))

    # Add metrics to store
    ms_acc_train_list.append(acc_train)
    ms_acc_test_list.append(acc_test)
    ms_loss_train_list.append(loss_train)
    ms_loss_test_list.append(loss_test)

    # plot loss curve
    plt.figure()
    plt.plot(range(len(ms_acc_train_list)), ms_acc_train_list)
    plt.plot(range(len(ms_acc_test_list)), ms_acc_test_list)
    plt.plot()
    plt.yticks(np.arange(0, 100, 10))
    plt.ylabel('Accuracy')
    plt.legend(['Training acc', 'Testing acc'])
    plt.savefig('./{}/fed_acc_{}_{}_{}_C{}_iid{}.png'.format(args.result_dir, args.dataset, args.model, args.epochs, args.frac, args.iid))

    # Write metric store into a CSV
    metrics_df = pd.DataFrame(
        {
            'Train acc': ms_acc_train_list,
            'Test acc': ms_acc_test_list,
            'Train loss': ms_loss_train_list,
            'Test loss': ms_loss_test_list
        })
    metrics_df.to_csv('./{}/fed_stats_{}_{}_{}_C{}_iid{}.csv'.format(args.result_dir, args.dataset, args.model, args.epochs, args.frac, args.iid), sep='\t')

    # torch.save(net.state_dict(), './{}/saved_model'.format(args.result_dir))
