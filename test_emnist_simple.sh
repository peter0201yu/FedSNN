python main_fed.py --snn --dataset EMNIST --num_classes 47 --img_size 28 --model simple --optimizer SGD --bs 32 --local_bs 32 --lr 0.1 --lr_reduce 5 --epochs 30 --local_ep 2 --eval_every 1 --alpha 0.05 --test_size 10000 --num_users 1000 --client_selection update_norm --frac 0.005 --candidate_frac 0.02 --gpu 0 --timesteps 10 --straggler_prob 0.0 --grad_noise_stdev 0.0 --result_dir emnist_1000c20c5_0.05_update_norm_rescaled --wandb emnist_1000c20c5_0.05_update_norm_rescaled
