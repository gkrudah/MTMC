#!/usr/bin/env python3
from argparse import ArgumentParser
from datetime import timedelta
from importlib import import_module
import logging.config
import os
from signal import SIGINT, SIGTERM
import sys
import time

import json
import numpy as np
import tensorflow as tf
from tensorflow.contrib import slim

import common
import lbtoolbox as lb
import loss
from nets import NET_CHOICES
from heads import HEAD_CHOICES

import imgaug as ia
from imgaug import augmenters as iaa
import cv2
import h5py
import scipy.spatial.distance
import pywt

parser = ArgumentParser(description='Train a ReID network.')

# Required.

parser.add_argument(
	'--experiment_root', required=True, type=common.writeable_directory,
	help='Location used to store checkpoints and dumped data.')

parser.add_argument(
	'--train_set',
	help='Path to the train_set csv file.')

parser.add_argument(
	'--image_root', type=common.readable_directory,
	help='Path that will be pre-pended to the filenames in the train_set csv.')

# Optional with sane defaults.

parser.add_argument(
	'--resume', action='store_true', default=False,
	help='When this flag is provided, all other arguments apart from the '
		 'experiment_root are ignored and a previously saved set of arguments '
		 'is loaded.')

parser.add_argument(
	'--model_name', default='resnet_v1_50', choices=NET_CHOICES,
	help='Name of the model to use.')

parser.add_argument(
	'--head_name', default='fc1024', choices=HEAD_CHOICES,
	help='Name of the head to use.')

parser.add_argument(
	'--embedding_dim', default=128, type=common.positive_int,
	help='Dimensionality of the embedding space.')

parser.add_argument(
	'--initial_checkpoint', default=None,
	help='Path to the checkpoint file of the pretrained network.')

# TODO move these defaults to the .sh script?
parser.add_argument(
	'--batch_p', default=32, type=common.positive_int,
	help='The number P used in the PK-batches')

parser.add_argument(
	'--batch_k', default=4, type=common.positive_int,
	help='The numberK used in the PK-batches')

parser.add_argument(
	'--net_input_height', default=256, type=common.positive_int,
	help='Height of the input directly fed into the network.')

parser.add_argument(
	'--net_input_width', default=128, type=common.positive_int,
	help='Width of the input directly fed into the network.')

parser.add_argument(
	'--pre_crop_height', default=288, type=common.positive_int,
	help='Height used to resize a loaded image. This is ignored when no crop '
		 'augmentation is applied.')

parser.add_argument(
	'--pre_crop_width', default=144, type=common.positive_int,
	help='Width used to resize a loaded image. This is ignored when no crop '
		 'augmentation is applied.')
# TODO end

parser.add_argument(
	'--loading_threads', default=8, type=common.positive_int,
	help='Number of threads used for parallel loading.')

parser.add_argument(
	'--margin', default='soft', type=common.float_or_string,
	help='What margin to use: a float value for hard-margin, "soft" for '
		 'soft-margin, or no margin if "none".')

parser.add_argument(
	'--metric', default='euclidean', choices=loss.cdist.supported_metrics,
	help='Which metric to use for the distance between embeddings.')

parser.add_argument(
	'--loss', default='batch_hard', choices=loss.LOSS_CHOICES.keys(),
	help='Enable the super-mega-advanced top-secret sampling stabilizer.')

# modified by ha (default 0.0003)
parser.add_argument(
	'--learning_rate', default=3e-4, type=common.positive_float,
	help='The initial value of the learning-rate, before it kicks in.')

# modified by ha (default 25000)
parser.add_argument(
	'--train_iterations', default=25000, type=common.positive_int,
	help='Number of training iterations.')

# modified by ha (default 15000)
parser.add_argument(
	'--decay_start_iteration', default=15000, type=int,
	help='At which iteration the learning-rate decay should kick-in.'
		 'Set to -1 to disable decay completely.')

parser.add_argument(
	'--checkpoint_frequency', default=12500, type=common.nonnegative_int,
	help='After how many iterations a checkpoint is stored. Set this to 0 to '
		 'disable intermediate storing. This will result in only one final '
		 'checkpoint.')

parser.add_argument(
	'--flip_augment', action='store_true', default=False,
	help='When this flag is provided, flip augmentation is performed.')

parser.add_argument(
	'--crop_augment', action='store_true', default=False,
	help='When this flag is provided, crop augmentation is performed. Based on'
		 'The `crop_height` and `crop_width` parameters. Changing this flag '
		 'thus likely changes the network input size!')

parser.add_argument(
	'--detailed_logs', action='store_true', default=False,
	help='Store very detailed logs of the training in addition to TensorBoard'
		 ' summaries. These are mem-mapped numpy files containing the'
		 ' embeddings, losses and FIDs seen in each batch during training.'
		 ' Everything can be re-constructed and analyzed that way.')

parser.add_argument(
	'--hard_pool_size', default=0, type=common.nonnegative_int,
	help='Number of IDs in hard identity pool')

parser.add_argument(
	'--train_embeddings',
	help='Path to pre-computed features of training set to be used for the hard identity pool')

parser.add_argument(
	'--augment', action='store_true', default=False,
	help='Data augmentation with imgaug')


def get_hard_id_pool(pids, dist, hard_pool_size):
	ids = pids
	hard_list = []
	seen_ids = []

	for ind in range(len(ids)):
		id = ids[ind]

		distances = dist[ind, :]
		order = np.argsort(distances)

		neg_inds = np.nonzero(ids[order] != id)[0]
		neg_ids = ids[order[neg_inds]]

		if id not in seen_ids:
			seen_ids.append(id)
			current_id_list = [id]
			index = -1
			while len(current_id_list) < hard_pool_size:  # np.minimum(num_people, len(neg_inds)):
				index = index + 1

				idx = index
				if neg_ids[idx] not in current_id_list:
					current_id_list.append(neg_ids[idx])

			# id, k hard, k-1 normal
			hard_list.append(current_id_list)

	return np.array(hard_list)


def sample_k_fids_for_pid(pid, all_fids, all_pids, batch_k):
	""" Given a PID, select K FIDs of that specific PID. """
	possible_fids = tf.boolean_mask(all_fids, tf.equal(all_pids, pid))

	# The following simply uses a subset of K of the possible FIDs
	# if more than, or exactly K are available. Otherwise, we first
	# create a padded list of indices which contain a multiple of the
	# original FID count such that all of them will be sampled equally likely.
	count = tf.shape(possible_fids)[0]
	padded_count = tf.cast(tf.ceil(batch_k / tf.cast(count, tf.float32)), tf.int32) * count
	full_range = tf.mod(tf.range(padded_count), count)

	# Sampling is always performed by shuffling and taking the first k.
	shuffled = tf.random_shuffle(full_range)
	selected_fids = tf.gather(possible_fids, shuffled[:batch_k])

	return selected_fids, tf.fill([batch_k], pid)


def sample_batch_ids_for_pid(pid, all_pids, batch_p, all_hard_pids=None):
	""" Given a PID, select P-1 PIDs for the batch, and return all P PIDs. """
	pid = tf.expand_dims(pid, axis=0)

	# Random pids
	random_p = batch_p - 1 if all_hard_pids is None else np.round(batch_p / 2).astype('int32')
	possible_pids = tf.boolean_mask(all_pids, tf.not_equal(all_pids, pid))
	count = tf.shape(possible_pids)[0]
	full_range = tf.range(count)
	shuffled = tf.random_shuffle(full_range)
	random_pids = tf.gather(possible_pids, shuffled[:random_p - 1])

	# Hard pids
	if all_hard_pids is not None:
		hard_p = batch_p - 1 - random_p
		row = tf.boolean_mask(all_hard_pids, tf.equal(all_hard_pids[:, 0], pid))
		possible_hard_pids = row[0][1:]
		count_hard = tf.shape(possible_hard_pids)[0]
		full_range_hard = tf.range(count_hard)
		shuffled_hard = tf.random_shuffle(full_range_hard)
		batch_hard_pids = tf.gather(possible_hard_pids, shuffled_hard[:hard_p])

	if all_hard_pids is None:
		batch_pids = tf.concat([pid, random_pids], axis=-1)
	else:
		batch_pids = tf.concat([pid, batch_hard_pids, random_pids], axis=-1)

	return batch_pids


def wvtransform(img):
	coeffs = pywt.dwt2(img, 'haar')

	threshold, np_coeffs = bayes_shrink(coeffs)
	cA, cH, cV, cD = soft_threshold(np_coeffs, threshold)
	detail = cH, cV, cD
	detail = tuple(map(tuple, detail))
	coeffs = cA, detail
	img = pywt.idwt2(coeffs, 'haar')

	return img


def soft_threshold(coeffs, threshold):
	return pywt.threshold(coeffs, value=threshold, mode='soft')


def bayes_shrink(coeffs):
	cA, (cH, cV, cD) = coeffs
	# Image.fromarray(cA.astype('uint8'), 'RGB').save("./cA.png")
	tmp = np.stack((cA, cH, cV, cD))
	sigV2 = (np.median(np.abs(cD)) / 0.6745) ** 2
	sigY2 = np.sum(np.power(tmp, 2)) / 4

	sigx = np.sqrt(max([(sigY2 - sigV2), 0]))

	if sigx != 0:
		threshold = sigV2 / sigx
	else:
		threshold = max(np.abs(coeffs))

	return threshold, tmp


def augment_images(img):
	img = np.array(img)
	# wavelet transform denosing
	img_r = img[:, :, 0]
	img_g = img[:, :, 1]
	img_b = img[:, :, 2]

	img_r = wvtransform(img_r)
	img_g = wvtransform(img_g)
	img_b = wvtransform(img_b)

	img = np.array([img_r, img_g, img_b])
	img = np.transpose(img, (1, 2, 0))

	img = img.astype('uint8')

	global seq_geo
	global seq_img

	img_content = img
	img_content_aug = seq_img.augment_image(img_content.astype('uint8'))
	img = img_content_aug.astype('float32')

	data_geo = img
	data_geo = seq_geo.augment_image(data_geo.astype('uint8'))
	img = data_geo.astype('float32')

	return img


def main():
	args = parser.parse_args()

	# Data augmentation
	global seq_geo
	global seq_img
	seq_geo = iaa.SomeOf((0, 5), [
		iaa.Fliplr(0.5),  # horizontally flip 50% of the images
		iaa.PerspectiveTransform(scale=(0, 0.075)),
		iaa.Affine(scale={"x": (0.8, 1.0), "y": (0.8, 1.0)},
				   rotate=(-5, 5),
				   translate_percent={"x": (-0.1, 0.1), "y": (-0.1, 0.1)},
				   ),  # rotate by -45 to +45 degrees),
		iaa.Crop(percent=(0, 0.125)),  # crop images from each side by 0 to 12.5% (randomly chosen)
		iaa.CoarsePepper(p=0.01, size_percent=0.1)
	], random_order=False)
	# Content transformation
	seq_img = iaa.SomeOf((0, 3), [
		iaa.GaussianBlur(sigma=(0, 1.0)),  # blur images with a sigma of 0 to 2.0
		iaa.ContrastNormalization(alpha=(0.9, 1.1)),
		iaa.Grayscale(alpha=(0, 0.2)),
		iaa.Multiply((0.9, 1.1))
	])

	# We store all arguments in a json file. This has two advantages:
	# 1. We can always get back and see what exactly that experiment was
	# 2. We can resume an experiment as-is without needing to remember all flags.
	args_file = os.path.join(args.experiment_root, 'args.json')
	if args.resume:
		if not os.path.isfile(args_file):
			raise IOError('`args.json` not found in {}'.format(args_file))

		print('Loading args from {}.'.format(args_file))
		with open(args_file, 'r') as f:
			args_resumed = json.load(f)
		args_resumed['resume'] = True  # This would be overwritten.

		# When resuming, we not only want to populate the args object with the
		# values from the file, but we also want to check for some possible
		# conflicts between loaded and given arguments.
		for key, value in args.__dict__.items():
			if key in args_resumed:
				resumed_value = args_resumed[key]
				if resumed_value != value:
					print('Warning: For the argument `{}` we are using the'
						  ' loaded value `{}`. The provided value was `{}`'
						  '.'.format(key, resumed_value, value))
					args.__dict__[key] = resumed_value
			else:
				print('Warning: A new argument was added since the last run:'
					  ' `{}`. Using the new value: `{}`.'.format(key, value))

	else:
		# If the experiment directory exists already, we bail in fear.
		if os.path.exists(args.experiment_root):
			if os.listdir(args.experiment_root):
				print('The directory {} already exists and is not empty.'
					  ' If you want to resume training, append --resume to'
					  ' your call.'.format(args.experiment_root))
				exit(1)
		else:
			os.makedirs(args.experiment_root)

		# Store the passed arguments for later resuming and grepping in a nice
		# and readable format.
		with open(args_file, 'w') as f:
			json.dump(vars(args), f, ensure_ascii=False, indent=2, sort_keys=True)

	log_file = os.path.join(args.experiment_root, "train")
	logging.config.dictConfig(common.get_logging_dict(log_file))
	log = logging.getLogger('train')

	# Also show all parameter values at the start, for ease of reading logs.
	log.info('Training using the following parameters:')
	for key, value in sorted(vars(args).items()):
		log.info('{}: {}'.format(key, value))

	# Check them here, so they are not required when --resume-ing.
	if not args.train_set:
		parser.print_help()
		log.error("You did not specify the `train_set` argument!")
		sys.exit(1)
	if not args.image_root:
		parser.print_help()
		log.error("You did not specify the required `image_root` argument!")
		sys.exit(1)

	# Load the data from the CSV file.
	pids, fids = common.load_dataset(args.train_set, args.image_root)
	max_fid_len = max(map(len, fids))  # We'll need this later for logfiles.

	# Load feature embeddings
	if args.hard_pool_size > 0:
		with h5py.File(args.train_embeddings, 'r') as f_train:
			train_embs = np.array(f_train['emb'])
			f_dists = scipy.spatial.distance.cdist(train_embs, train_embs)
			hard_ids = get_hard_id_pool(pids, f_dists, args.hard_pool_size)

	# Setup a tf.Dataset where one "epoch" loops over all PIDS.
	# PIDS are shuffled after every epoch and continue indefinitely.
	unique_pids = np.unique(pids)
	dataset = tf.data.Dataset.from_tensor_slices(unique_pids)
	dataset = dataset.shuffle(len(unique_pids))

	# Constrain the dataset size to a multiple of the batch-size, so that
	# we don't get overlap at the end of each epoch.
	if args.hard_pool_size == 0:
		dataset = dataset.take((len(unique_pids) // args.batch_p) * args.batch_p)
		dataset = dataset.repeat(None)  # Repeat forever. Funny way of stating it.

	else:
		dataset = dataset.repeat(None)  # Repeat forever. Funny way of stating it.
		dataset = dataset.map(lambda pid: sample_batch_ids_for_pid(
			pid, all_pids=pids, batch_p=args.batch_p, all_hard_pids=hard_ids))
		# Unbatch the P PIDs
		dataset = dataset.apply(tf.contrib.data.unbatch())

	# For every PID, get K images.
	dataset = dataset.map(lambda pid: sample_k_fids_for_pid(
		pid, all_fids=fids, all_pids=pids, batch_k=args.batch_k))

	# Ungroup/flatten the batches for easy loading of the files.
	dataset = dataset.apply(tf.contrib.data.unbatch())

	# Convert filenames to actual image tensors.
	net_input_size = (args.net_input_height, args.net_input_width)
	pre_crop_size = (args.pre_crop_height, args.pre_crop_width)
	dataset = dataset.map(
		lambda fid, pid: common.fid_to_image(
			fid, pid, image_root=args.image_root,
			image_size=pre_crop_size if args.crop_augment else net_input_size),
		num_parallel_calls=args.loading_threads)

	# Augment the data if specified by the arguments.
	if args.augment == False:
		dataset = dataset.map(
			lambda im, fid, pid: common.fid_to_image(
				fid, pid, image_root=args.image_root,
				image_size=pre_crop_size if args.crop_augment else net_input_size),  # Ergys
			num_parallel_calls=args.loading_threads)

		if args.flip_augment:
			dataset = dataset.map(
				lambda im, fid, pid: (tf.image.random_flip_left_right(im), fid, pid))
		if args.crop_augment:
			dataset = dataset.map(
				lambda im, fid, pid: (tf.random_crop(im, net_input_size + (3,)), fid, pid))
	else:
		dataset = dataset.map(
			lambda im, fid, pid: common.fid_to_image(
				fid, pid, image_root=args.image_root,
				image_size=net_input_size),
			num_parallel_calls=args.loading_threads)

		dataset = dataset.map(
			lambda im, fid, pid: (tf.py_func(augment_images, [im], [tf.float32]), fid, pid))
		dataset = dataset.map(
			lambda im, fid, pid: (tf.reshape(im[0], (args.net_input_height, args.net_input_width, 3)), fid, pid))

	# Group it back into PK batches.
	batch_size = args.batch_p * args.batch_k
	dataset = dataset.batch(batch_size)

	# Overlap producing and consuming for parallelism.
	dataset = dataset.prefetch(batch_size * 2)

	# Since we repeat the data infinitely, we only need a one-shot iterator.
	images, fids, pids = dataset.make_one_shot_iterator().get_next()
	print(images)

	# Create the model and an embedding head.
	model = import_module('nets.' + args.model_name)
	head = import_module('heads.' + args.head_name)

	# Feed the image through the model. The returned `body_prefix` will be used
	# further down to load the pre-trained weights for all variables with this
	# prefix.
	endpoints, body_prefix = model.endpoints(images, is_training=True)
	with tf.name_scope('head'):
		endpoints = head.head(endpoints, args.embedding_dim, is_training=True)

	# Create the loss in two steps:
	# 1. Compute all pairwise distances according to the specified metric.
	# 2. For each anchor along the first dimension, compute its loss.
	dists = loss.cdist(endpoints['emb'], endpoints['emb'], metric=args.metric)
	losses, train_top1, prec_at_k, _, neg_dists, pos_dists = loss.LOSS_CHOICES[args.loss](
		dists, pids, args.margin, batch_precision_at_k=args.batch_k - 1)

	# Count the number of active entries, and compute the total batch loss.
	num_active = tf.reduce_sum(tf.cast(tf.greater(losses, 1e-5), tf.float32))
	loss_mean = tf.reduce_mean(losses)

	# Some logging for tensorboard.
	tf.summary.histogram('loss_distribution', losses)
	tf.summary.scalar('loss', loss_mean)
	tf.summary.scalar('batch_top1', train_top1)
	tf.summary.scalar('batch_prec_at_{}'.format(args.batch_k - 1), prec_at_k)
	tf.summary.scalar('active_count', num_active)
	tf.summary.histogram('embedding_dists', dists)
	tf.summary.histogram('embedding_pos_dists', pos_dists)
	tf.summary.histogram('embedding_neg_dists', neg_dists)
	tf.summary.histogram('embedding_lengths',
						 tf.norm(endpoints['emb_raw'], axis=1))

	# Create the mem-mapped arrays in which we'll log all training detail in
	# addition to tensorboard, because tensorboard is annoying for detailed
	# inspection and actually discards data in histogram summaries.
	if args.detailed_logs:
		log_embs = lb.create_or_resize_dat(
			os.path.join(args.experiment_root, 'embeddings'),
			dtype=np.float32, shape=(args.train_iterations, batch_size, args.embedding_dim))
		log_loss = lb.create_or_resize_dat(
			os.path.join(args.experiment_root, 'losses'),
			dtype=np.float32, shape=(args.train_iterations, batch_size))
		log_fids = lb.create_or_resize_dat(
			os.path.join(args.experiment_root, 'fids'),
			dtype='S' + str(max_fid_len), shape=(args.train_iterations, batch_size))

	# These are collected here before we add the optimizer, because depending
	# on the optimizer, it might add extra slots, which are also global
	# variables, with the exact same prefix.
	model_variables = tf.get_collection(
		tf.GraphKeys.GLOBAL_VARIABLES, body_prefix)

	# Define the optimizer and the learning-rate schedule.
	# Unfortunately, we get NaNs if we don't handle no-decay separately.
	# modified by ha (default decay rate 0.001)
	global_step = tf.Variable(0, name='global_step', trainable=False)
	if 0 <= args.decay_start_iteration < args.train_iterations:
		learning_rate = tf.train.exponential_decay(
			args.learning_rate,
			tf.maximum(0, global_step - args.decay_start_iteration),
			args.train_iterations - args.decay_start_iteration, 0.001)
	else:
		learning_rate = args.learning_rate
	tf.summary.scalar('learning_rate', learning_rate)
	optimizer = tf.train.AdamOptimizer(learning_rate)
	# Feel free to try others!
	# optimizer = tf.train.AdadeltaOptimizer(learning_rate)

	# Update_ops are used to update batchnorm stats.
	with tf.control_dependencies(tf.get_collection(tf.GraphKeys.UPDATE_OPS)):
		train_op = optimizer.minimize(loss_mean, global_step=global_step)

	# Define a saver for the complete model.
	checkpoint_saver = tf.train.Saver(max_to_keep=0)

	with tf.Session() as sess:
		if args.resume:
			# In case we're resuming, simply load the full checkpoint to init.
			last_checkpoint = tf.train.latest_checkpoint(args.experiment_root)
			log.info('Restoring from checkpoint: {}'.format(last_checkpoint))
			checkpoint_saver.restore(sess, last_checkpoint)
		else:
			# But if we're starting from scratch, we may need to load some
			# variables from the pre-trained weights, and random init others.
			sess.run(tf.global_variables_initializer())
			if args.initial_checkpoint is not None:
				# saver = tf.train.Saver(model_variables)
				saver = tf.train.import_meta_graph('./mobilenet/mobilenet_v2_1.4_224.ckpt.meta')
				saver.restore(sess, args.initial_checkpoint)

			# In any case, we also store this initialization as a checkpoint,
			# such that we could run exactly reproduceable experiments.
			checkpoint_saver.save(sess, os.path.join(
				args.experiment_root, 'checkpoint'), global_step=0)

		merged_summary = tf.summary.merge_all()
		summary_writer = tf.summary.FileWriter(args.experiment_root, sess.graph)

		start_step = sess.run(global_step)
		log.info('Starting training from iteration {}.'.format(start_step))

		# Finally, here comes the main-loop. This `Uninterrupt` is a handy
		# utility such that an iteration still finishes on Ctrl+C and we can
		# stop the training cleanly.
		with lb.Uninterrupt(sigs=[SIGINT, SIGTERM], verbose=True) as u:
			for i in range(start_step, args.train_iterations):

				# Compute gradients, update weights, store logs!
				start_time = time.time()
				_, summary, step, b_prec_at_k, b_embs, b_loss, b_fids = \
					sess.run([train_op, merged_summary, global_step,
							  prec_at_k, endpoints['emb'], losses, fids])
				elapsed_time = time.time() - start_time

				# Compute the iteration speed and add it to the summary.
				# We did observe some weird spikes that we couldn't track down.
				summary2 = tf.Summary()
				summary2.value.add(tag='secs_per_iter', simple_value=elapsed_time)
				summary_writer.add_summary(summary2, step)
				summary_writer.add_summary(summary, step)

				if args.detailed_logs:
					log_embs[i], log_loss[i], log_fids[i] = b_embs, b_loss, b_fids

				# Do a huge print out of the current progress.
				seconds_todo = (args.train_iterations - step) * elapsed_time
				log.info('iter:{:6d}, loss min|avg|max: {:.3f}|{:.3f}|{:6.3f}, '
						 'batch-p@{}: {:.2%}, ETA: {} ({:.2f}s/it)'.format(
					step,
					float(np.min(b_loss)),
					float(np.mean(b_loss)),
					float(np.max(b_loss)),
					args.batch_k - 1, float(b_prec_at_k),
					timedelta(seconds=int(seconds_todo)),
					elapsed_time))
				sys.stdout.flush()
				sys.stderr.flush()

				# Save a checkpoint of training every so often.
				if (args.checkpoint_frequency > 0 and
						step % args.checkpoint_frequency == 0):
					checkpoint_saver.save(sess, os.path.join(
						args.experiment_root, 'checkpoint'), global_step=step)

				# Stop the main-loop at the end of the step, if requested.
				if u.interrupted:
					log.info("Interrupted on request!")
					break

		# Store one final checkpoint. This might be redundant, but it is crucial
		# in case intermediate storing was disabled and it saves a checkpoint
		# when the process was interrupted.
		checkpoint_saver.save(sess, os.path.join(
			args.experiment_root, 'checkpoint'), global_step=step)


if __name__ == '__main__':
	main()
