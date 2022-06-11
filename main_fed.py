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
from models.Update import LocalUpdate
from models.Fed import FedLearn
from models.Fed import model_deviation
from models.test import test_img
import models.vgg as ann_models
import models.resnet as resnet_models
import models.vgg_spiking_bntt as snn_models_bntt
import models.simple_conv as simple_model
import models.simple_conv_mnist as simple_model_mnist
import models.client_selection as client_selection

import tables
import yaml
import glob
import json

from PIL import Image

from pysnn.datasets import nmnist_train_test

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
        if args.iid:
            dict_users = cifar_iid(dataset_train, args.num_users)
        else:
            dict_users = cifar_non_iid(dataset_train, args.num_classes, args.num_users)
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
    else:
        exit('Error: unrecognized dataset')
    # img_size = dataset_train[0][0].shape

    # build model
    model_args = {'args': args}
    if args.model[0:3].lower() == 'vgg':
        if args.snn:
            model_args = {'num_cls': args.num_classes, 'timesteps': args.timesteps}
            net_glob = snn_models_bntt.SNN_VGG9_BNTT(**model_args).cuda()
        else:
            model_args = {'vgg_name': args.model, 'labels': args.num_classes, 'dataset': args.dataset, 'kernel_size': 3, 'dropout': args.dropout}
            net_glob = ann_models.VGG(**model_args).cuda()
    elif args.model[0:6].lower() == 'resnet':
        if args.snn:
            pass
        else:
            model_args = {'num_cls': args.num_classes}
            net_glob = resnet_models.Network(**model_args).cuda()
    elif args.model == 'simple':
        model_args = {'num_cls': args.num_classes, 'timesteps': args.timesteps}
        if args.dataset == 'MNIST':
            model_args['img_size'] = 28
            net_glob = simple_model_mnist.Simple_Net_Mnist(**model_args).cuda()
        else:
            net_glob = simple_model.Simple_Net(**model_args).cuda()
    else:
        exit('Error: unrecognized model')
    print(net_glob)

    # copy weights
    if args.pretrained_model:
        net_glob.load_state_dict(torch.load(args.pretrained_model, map_location='cpu'))

    net_glob = nn.DataParallel(net_glob)
    # training
    loss_train_list = []
    cv_loss, cv_acc = [], []
    val_loss_pre, counter = 0, 0
    net_best = None
    best_loss = None
    val_acc_list, net_list = [], []

    # metrics to store
    ms_acc_train_list, ms_loss_train_list = [], []
    ms_acc_test_list, ms_loss_test_list = [], []
    ms_num_client_list, ms_tot_comm_cost_list, ms_avg_comm_cost_list, ms_max_comm_cost_list = [], [], [], []
    ms_tot_nz_grad_list, ms_avg_nz_grad_list, ms_max_nz_grad_list = [], [], []
    # ms_model_deviation = []

    # testing
    net_glob.eval()
    # acc_train, loss_train = test_img(net_glob, dataset_train, args)
    # acc_test, loss_test = test_img(net_glob, dataset_test, args)
    # print("Initial Training accuracy: {:.2f}".format(acc_train))
    # print("Initial Testing accuracy: {:.2f}".format(acc_test))
    acc_train, loss_train = 0, 0
    acc_test, loss_test = 0, 0
    # Add metrics to store
    ms_acc_train_list.append(acc_train)
    ms_acc_test_list.append(acc_test)
    ms_loss_train_list.append(loss_train)
    ms_loss_test_list.append(loss_test)

    # Define LR Schedule
    values = args.lr_interval.split()
    lr_interval = []
    for value in values:
        lr_interval.append(int(float(value)*args.epochs))

    # Define Fed Learn object
    fl = FedLearn(args)

    client_selection_history = []

    for iter in range(args.epochs):
        net_glob.train()
        w_locals_selected, loss_locals_selected = [], []
        w_locals_all, loss_locals_all = [], []
        trained_data_size_all = []
        
        candidates = [idx for idx in range(args.num_users) if len(dict_users[idx]) > args.bs]
        if args.client_selection == "random" and args.candidate_frac:
            candidate_num = max(int(args.candidate_frac * args.num_users),1)
            candidates = np.random.choice(range(args.num_users), size=candidate_num, replace=False)
        elif args.candidate_frac:
            # Choose preliminary candidate client set: improve speed for client selection algorithm
            dataset_size = sum([len(dict_users[i]) for i in range(args.num_users)])
            probs = [float(len(dict_users[i]))/dataset_size for i in range(args.num_users)]
            candidate_num = max(int(args.candidate_frac * args.num_users),1)
            candidates = np.random.choice(range(args.num_users), size=candidate_num, replace=False, p=probs)

        print("candidate clients: ", candidates)

        # for idx in idxs_users:
        # Do local update in all the clients # Not required (local updates in only the selected clients is enough) for normal experiments but neeeded for model deviation analysis
        for idx in candidates:
            print("len(dict_users[idx]): ", len(dict_users[idx]))
            local = LocalUpdate(args=args, dataset=dataset_train, idxs=dict_users[idx]) # idxs needs the list of indices assigned to this particular client
            model_copy = type(net_glob.module)(**model_args) # get a new instance
            model_copy = nn.DataParallel(model_copy)
            model_copy.load_state_dict(net_glob.state_dict()) # copy weights and stuff
            w, loss, trained_data_size = local.train(net=model_copy.to(args.device))
            w_locals_all.append(copy.deepcopy(w))
            loss_locals_all.append(copy.deepcopy(loss))
            trained_data_size_all.append(trained_data_size)
        
        # print("training data distribution: ", trained_data_size_all)
        
        m = max(int(args.frac * args.num_users), 1)
        if not args.client_selection or args.client_selection == "random":
            idxs_users = client_selection.random(len(candidates), m)
        elif args.client_selection == "biggest_loss":
            idxs_users = client_selection.biggest_loss(loss_locals_all, len(candidates), m)
        elif args.client_selection == "grad_diversity":
            delta_w_locals_all = []
            w_init = net_glob.state_dict()
            for i in range(len(w_locals_all)):
                delta_w = {}
                for k in w_init.keys():
                    delta_w[k] = w_locals_all[i][k] - w_init[k]
                delta_w_locals_all.append(delta_w)
            idxs_users = client_selection.grad_diversity(delta_w_locals_all, len(candidates), m)
        elif args.client_selection == "update_norm":
            delta_w_locals_all = []
            w_init = net_glob.state_dict()
            for i in range(len(w_locals_all)):
                delta_w = {}
                for k in w_init.keys():
                    delta_w[k] = w_locals_all[i][k] - w_init[k]
                delta_w_locals_all.append(delta_w)
            # Need to find number of training examples of each data size
            idxs_users, delta_w_locals_all_rescaled = client_selection.update_norm(delta_w_locals_all, trained_data_size_all, len(candidates), m)
            
            # update new weights:
            for i in range(len(w_locals_all)):
                for k in w_init.keys():
                    w_locals_all[i][k] = w_init[k] + delta_w_locals_all_rescaled[i][k]

        # idxs_users gives the client's index in the candidates list, need to convert
        print("Selected clients:", [candidates[idx] for idx in idxs_users])
        client_selection_history.append([candidates[idx] for idx in idxs_users])

        for idx in idxs_users:
            w_locals_selected.append(copy.deepcopy(w_locals_all[idx]))
            loss_locals_selected.append(copy.deepcopy(loss_locals_all[idx]))
        
        # model_dev_list = model_deviation(w_locals_all, net_glob.state_dict())
        # ms_model_deviation.append(model_dev_list)

        # update global weights
        w_glob = fl.FedAvg(w_locals_selected, w_init = net_glob.state_dict())
        
        # copy weight to net_glob
        net_glob.load_state_dict(w_glob)
 
        loss_avg = sum(loss_locals_selected) / len(loss_locals_selected)
        print('Round {:3d}, Average loss {:.3f}'.format(iter, loss_avg))
        loss_train_list.append(loss_avg)
 
        if iter % args.eval_every == 0:
            # testing
            net_glob.eval()
            acc_train, loss_train = test_img(net_glob, dataset_train, args)
            print("Round {:d}, Training accuracy: {:.2f}".format(iter, acc_train))
            acc_test, loss_test = test_img(net_glob, dataset_test, args)
            print("Round {:d}, Testing accuracy: {:.2f}".format(iter, acc_test))
 
            # Add metrics to store
            ms_acc_train_list.append(acc_train)
            ms_acc_test_list.append(acc_test)
            ms_loss_train_list.append(loss_train)
            ms_loss_test_list.append(loss_test)

        if iter in lr_interval:
            args.lr = args.lr/args.lr_reduce

    Path('./{}'.format(args.result_dir)).mkdir(parents=True, exist_ok=True)
    # plot loss curve
    plt.figure()
    plt.plot(range(len(loss_train_list)), loss_train_list)
    plt.ylabel('train_loss')
    plt.savefig('./{}/fed_loss_{}_{}_{}_C{}_iid{}.png'.format(args.result_dir,args.dataset, args.model, args.epochs, args.frac, args.iid))

    # testing
    net_glob.eval()
    acc_train, loss_train = test_img(net_glob, dataset_train, args)
    print("Final Training accuracy: {:.2f}".format(acc_train))
    acc_test, loss_test = test_img(net_glob, dataset_test, args)
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

    torch.save(net_glob.module.state_dict(), './{}/saved_model'.format(args.result_dir))

    # fn = './{}/model_deviation_{}_{}_{}_C{}_iid{}.json'.format(args.result_dir, args.dataset, args.model, args.epochs, args.frac, args.iid)
    # with open(fn, 'w') as f:
    #     json.dump(ms_model_deviation, f)

    # Save client selection history
    f = open("./{}/client_selection_history.txt".format(args.result_dir), "w")
    f.write("Client selection history\n")
    for i in range(len(client_selection_history)):
        f.write("Round {}, selected: {} \n".format(i, client_selection_history[i]))
    f.close()