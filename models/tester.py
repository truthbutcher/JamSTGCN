
"""This file implement the testing process of STGCN model.
"""
import os
import sys
import time
import argparse
import numpy as np
import pandas as pd
import paddle
import paddle.fluid as fluid
import paddle.fluid.layers as fl
import pgl
from pgl.utils.logger import log

from data_loader.data_utils import gen_batch
from utils.math_utils import evaluation


def multi_pred(gf, model, y_pred, seq, batch_size, \
        n_his, n_pred, step_idx, dynamic_batch=True):
    """multi step prediction"""
    pred_list = []
    for i in gen_batch(
            seq, min(batch_size, len(seq)), dynamic_batch=dynamic_batch):

        # Note: use np.copy() to avoid the modification of source data.
        test_seq = np.copy(i[:, :n_his*n_pred+n_his, :, :]).astype(np.int32)
        
        gt_seq = np.copy(i[:, -n_pred:, :, :]).astype(np.int32)

        graph = gf.build_graph(i[:, n_his*n_pred:n_his*(n_pred+1), :, :])
        step_list = []

        for j in range(n_pred):
            
            input_seq = np.concatenate((test_seq, gt_seq[:,j:j+1,:,:]),axis=1)

            pred, _ = model(graph, input_seq)
            if isinstance(pred, list):
                pred = np.array(pred[0])
            
            test_seq[:, n_his*n_pred:n_his*(n_pred+1)-1, :, :] = test_seq[:, n_his*n_pred+1:n_his*(n_pred+1), :, :]
            
            pred = np.expand_dims(pred, axis=-1)
            
            test_seq[:, n_his*(n_pred+1)-1, :, :] = pred
            step_list.append(pred)
        pred_list.append(step_list)
    #  pred_array -> [n_pred, len(seq), n_route, C_0)
    pred_array = np.concatenate(pred_list, axis=1)
    return pred_array, pred_array.shape[1]


def model_inference(gf, model, pred, inputs, args, step_idx,
                    min_va_val, min_val):
    """inference model"""
    x_val, x_test, x_stats = inputs.get_data('val'), inputs.get_data(
        'test'), inputs.get_stats()

    if args.n_his + args.n_pred > x_val.shape[1]:
        raise ValueError(
            f'ERROR: the value of n_pred "{args.n_pred}" exceeds the length limit.'
        )

    # y_val shape: [n_pred, len(x_val), n_route, C_0)
    y_val, len_val = multi_pred(gf, model, pred, \
            x_val, args.batch_size, args.n_his, args.n_pred, step_idx)

    evl_val = evaluation(x_val[0:len_val, step_idx + args.n_his*(args.n_pred+1), :, :],
                         y_val[step_idx], x_stats)
    
    
    # chks: indicator that reflects the relationship of values between evl_val and min_va_val.
    chks = [evl_val[0] > min_va_val[0], False, False]
    # update the metric on test set, if model's performance got improved on the validation.
    if sum(chks):
        print("updated")
        min_va_val[chks] = evl_val[chks]
        y_pred, len_pred = multi_pred(gf, model, pred, \
                x_test, args.batch_size, args.n_his, args.n_pred, step_idx)

        evl_pred = evaluation(x_test[0:len_pred, step_idx + args.n_his*(args.n_pred+1), :, :],
                              y_pred[step_idx], x_stats)
        min_val = evl_pred

    return min_va_val, min_val


def model_test(gf, model, pred, inputs, args):
    """test model"""
    n_his = args.n_his
    n_pred = args.n_pred
    if args.inf_mode == 'sep':
        # for inference mode 'sep', the type of step index is int.
        step_idx = args.n_pred - 1
        tmp_idx = [step_idx]
    elif args.inf_mode == 'merge':
        # for inference mode 'merge', the type of step index is np.ndarray.
        step_idx = tmp_idx = np.arange(3, args.n_pred + 1, 3) - 1
        #print(step_idx)
    else:
        raise ValueError(f'ERROR: test mode "{args.inf_mode}" is not defined.')

    x_test, x_stats = inputs.get_data('test'), inputs.get_stats()
    y_test, len_test = multi_pred(gf, model, pred, \
            x_test, args.batch_size, args.n_his, args.n_pred, step_idx)

    # save result
    gt = x_test[0:len_test, args.n_his*(n_pred+1):, :, :].reshape(-1, args.n_route)
    y_pred = y_test.reshape(-1, args.n_route)
    
    inf = x_test[0:len_test, n_his:n_his*n_pred+1:n_his, :, :].reshape(-1, args.n_route)
    
    np.savetxt(
        os.path.join(args.output_path, "groundtruth.csv"),
        gt.astype(np.int32),
        fmt='%d',
        delimiter=',')
    np.savetxt(
        os.path.join(args.output_path, "prediction.csv"),
        y_pred.astype(np.int32),
        fmt='%d',
        delimiter=",")
    np.savetxt(
        os.path.join(args.output_path, "infer.csv"),
        inf.astype(np.int32),
        fmt='%d',
        delimiter=',')
    for i in range(step_idx + 1):
        evl = evaluation(x_test[0:len_test, i+1+args.n_his*(n_pred+1)-1, :, :],
                         y_test[i], x_stats)

        evg = evaluation(x_test[0:len_test, i+1+args.n_his*(n_pred+1)-1, :, :],
                         x_test[0:len_test, (i+1)*n_his-1, :, :], x_stats)
        
        test_eva = x_test[0:len_test, i+1+args.n_his*(n_pred+1)-1, :, :]
        
        evo = evaluation(test_eva, np.ones_like(test_eva), x_stats)

        tf = evo
        
        te = evl
        print(
            f'Test set: ACC {te[0]:7.3%}; MAE  {te[1]:4.3f}; RMSE {te[2]:6.3f}.'
        )
        print(
            f'Benchmark: ACC {tf[0]:7.3%}; MAE  {tf[1]:4.3f}; RMSE {tf[2]:6.3f}.'
        )
    return y_test
