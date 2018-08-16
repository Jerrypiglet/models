# Copyright 2018 The TensorFlow Authors All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
"""Training script for the DeepLab model.

See model.py for more details and usage.
"""
import warnings
warnings.filterwarnings("ignore")
import six
import os
import tensorflow as tf
from deeplab import common
from deeplab import model
from deeplab.datasets import regression_dataset
from deeplab.utils import input_generator
from deeplab.utils import train_utils
from deployment import model_deploy
import numpy as np
np.set_printoptions(threshold=np.nan)
np.set_printoptions(precision=4)

from deeplab.core import preprocess_utils

slim = tf.contrib.slim

prefetch_queue = slim.prefetch_queue

flags = tf.app.flags

FLAGS = flags.FLAGS

# Settings for multi-GPUs/multi-replicas training.

flags.DEFINE_integer('num_clones', 1, 'Number of clones to deploy.')

flags.DEFINE_boolean('clone_on_cpu', False, 'Use CPUs to deploy clones.')

flags.DEFINE_integer('num_replicas', 1, 'Number of worker replicas.')

flags.DEFINE_integer('startup_delay_steps', 15,
                     'Number of training steps between replicas startup.')

flags.DEFINE_integer('num_ps_tasks', 0,
                     'The number of parameter servers. If the value is 0, then '
                     'the parameters are handled locally by the worker.')

flags.DEFINE_string('master', '', 'BNS name of the tensorflow server')

flags.DEFINE_integer('task', 0, 'The task ID.')

# Settings for logging.

flags.DEFINE_string('task_name', 'tmp',
                    'Task name; will be appended to FLAGS.train_logdir to log files.')

flags.DEFINE_string('restore_name', None,
                    'Task name to restore; will be appended to FLAGS.train_logdir to log files.')

flags.DEFINE_string('base_logdir', None,
                    'Where the checkpoint and logs are stored (base dir).')

flags.DEFINE_string('train_logdir', None,
                    'Where the checkpoint and logs are stored.')

flags.DEFINE_string('restore_logdir', None,
                    'Where the checkpoint and logs are REstored.')

flags.DEFINE_integer('log_steps', 10,
                     'Display logging information at every log_steps.')

flags.DEFINE_boolean('if_val', False,
                     'If we VALIDATE the model.')

flags.DEFINE_integer('val_interval_steps', 10,
                     'How often, in steps, we VALIDATE the model.')

flags.DEFINE_integer('save_interval_secs', 300,
                     'How often, in seconds, we save the model to disk.')

flags.DEFINE_integer('save_summaries_secs', 30,
                     'How often, in seconds, we compute the summaries.')

flags.DEFINE_boolean('save_summaries_images', False,
                     'Save sample inputs, labels, and semantic predictions as images to summary.')

flags.DEFINE_boolean('if_print_tensors', False,
                     'If we print all the tensors and their names.')

# Settings for training strategy.

flags.DEFINE_enum('learning_policy', 'poly', ['poly', 'step'],
                  'Learning rate policy for training.')

# Use 0.007 when training on PASCAL augmented training set, train_aug. When
# fine-tuning on PASCAL trainval set, use learning rate=0.0001.
flags.DEFINE_float('base_learning_rate', .0001,
                   'The base learning rate for model training.')

flags.DEFINE_float('learning_rate_decay_factor', 0.1,
                   'The rate to decay the base learning rate.')

flags.DEFINE_integer('learning_rate_decay_step', 5000,
                     'Decay the base learning rate at a fixed step.')

flags.DEFINE_float('learning_power', 0.9,
                   'The power value used in the poly learning policy.')

flags.DEFINE_integer('training_number_of_steps', 300000,
                     'The number of steps used for training')

flags.DEFINE_float('momentum', 0.9, 'The momentum value to use')

# When fine_tune_batch_norm=True, use at least batch size larger than 12
# (batch size more than 16 is better). Otherwise, one could use smaller batch
# size and set fine_tune_batch_norm=False.
flags.DEFINE_integer('train_batch_size', 8,
                     'The number of images in each batch during training.')

# For weight_decay, use 0.00004 for MobileNet-V2 or Xcpetion model variants.
# Use 0.0001 for ResNet model variants.
flags.DEFINE_boolean('if_discrete_loss', True,
                     'Use discrete regression + classification loss.')

flags.DEFINE_float('weight_decay', 0.00004,
                   'The value of the weight decay for training.')

flags.DEFINE_multi_integer('train_crop_size', [513, 513],
                           'Image crop size [height, width] during training.')

flags.DEFINE_float('last_layer_gradient_multiplier', 1.0,
                   'The gradient multiplier for last layers, which is used to '
                   'boost the gradient of last layers if the value > 1.')

flags.DEFINE_boolean('upsample_logits', True,
                     'Upsample logits during training.')

# Settings for fine-tuning the network.

flags.DEFINE_boolean('if_restore', False,
                    'Whether to restore the logged checkpoint.')

flags.DEFINE_string('tf_initial_checkpoint', None,
                    'The initial checkpoint in tensorflow format.')

# Set to False if one does not want to re-use the trained classifier weights.
flags.DEFINE_boolean('initialize_last_layer', True,
                     'Initialize the last layer.')

flags.DEFINE_boolean('last_layers_contain_logits_only', False,
                     'Only consider logits as last layers or not.')

flags.DEFINE_integer('slow_start_step', 0,
                     'Training model with small learning rate for few steps.')

flags.DEFINE_float('slow_start_learning_rate', 1e-4,
                   'Learning rate employed during slow start.')

# Set to True if one wants to fine-tune the batch norm parameters in DeepLabv3.
# Set to False and use small batch size to save GPU memory.
flags.DEFINE_boolean('fine_tune_feature_extractor', True,
                     'Fine tune the feature extractors or not.')

flags.DEFINE_boolean('fine_tune_batch_norm', True,
                     'Fine tune the batch norm parameters or not.')

flags.DEFINE_float('min_scale_factor', 1.0,
                   'Mininum scale factor for data augmentation.')

flags.DEFINE_float('max_scale_factor', 1.0,
                   'Maximum scale factor for data augmentation.')

flags.DEFINE_float('scale_factor_step_size', 0.,
                   'Scale factor step size for data augmentation.')

# For `xception_65`, use atrous_rates = [12, 24, 36] if output_stride = 8, or
# rates = [6, 12, 18] if output_stride = 16. For `mobilenet_v2`, use None. Note
# one could use different atrous_rates/output_stride during training/evaluation.
flags.DEFINE_multi_integer('atrous_rates', None,
                           'Atrous rates for atrous spatial pyramid pooling.')

flags.DEFINE_integer('output_stride', 16,
                     'The ratio of input to output spatial resolution.')

# Dataset settings.
flags.DEFINE_string('dataset', 'apolloscape',
                    'Name of the segmentation dataset.')

flags.DEFINE_string('train_split', 'train',
                    'Which split of the dataset to be used for training')

flags.DEFINE_string('val_split', 'val',
                    'Which split of the dataset to be used for validation')

flags.DEFINE_string('dataset_dir', 'deeplab/datasets/apolloscape', 'Where the dataset reside.')


from build_deeplab import _build_deeplab

def main(unused_argv):
  FLAGS.train_logdir = FLAGS.base_logdir + '/' + FLAGS.task_name
  if FLAGS.restore_name == None:
      FLAGS.restore_logdir = FLAGS.train_logdir
  else:
      FLAGS.restore_logdir = FLAGS.base_logdir + '/' + FLAGS.restore_name

  tf.logging.set_verbosity(tf.logging.INFO)

  # Get logging dir ready.
  if not(os.path.isdir(FLAGS.train_logdir)):
      tf.gfile.MakeDirs(FLAGS.train_logdir)
  elif len(os.listdir(FLAGS.train_logdir) ) != 0:
      if not(FLAGS.if_restore):
          if_delete_all = raw_input('#### The log folder %s exists and non-empty; delete all logs? [y/n] '%FLAGS.train_logdir)
          if if_delete_all == 'y':
              os.system('rm -rf %s/*'%FLAGS.train_logdir)
              print '==== Log folder emptied.'
      else:
          print '==== Log folder exists; not emptying it because we need to restore from it.'
  tf.logging.info('==== Logging in dir:%s; Training on %s set', FLAGS.train_logdir, FLAGS.train_split)

  # Set up deployment (i.e., multi-GPUs and/or multi-replicas).
  config = model_deploy.DeploymentConfig(
      num_clones=FLAGS.num_clones,
      clone_on_cpu=FLAGS.clone_on_cpu,
      replica_id=FLAGS.task,
      num_replicas=FLAGS.num_replicas,
      num_ps_tasks=FLAGS.num_ps_tasks) # /device:CPU:0

  # Split the batch across GPUs.
  assert FLAGS.train_batch_size % config.num_clones == 0, (
      'Training batch size not divisble by number of clones (GPUs).')
  clone_batch_size = FLAGS.train_batch_size // config.num_clones

  # Get dataset-dependent information.
  dataset = regression_dataset.get_dataset(
      FLAGS.dataset, FLAGS.train_split, dataset_dir=FLAGS.dataset_dir)
  dataset_val = regression_dataset.get_dataset(
      FLAGS.dataset, FLAGS.val_split, dataset_dir=FLAGS.dataset_dir)
  print '#### The data has size:', dataset.num_samples, dataset_val.num_samples

  with tf.Graph().as_default() as graph:
    with tf.device(config.inputs_device()):
      bin_range = [np.linspace(r[0], r[1], num=b).tolist() for r, b in zip(dataset.pose_range, dataset.bin_nums[:7])]
      outputs_to_num_classes = {}
      outputs_to_indices = {}
      for output, bin_num, idx in zip(dataset.output_names, dataset.bin_nums,range(len(dataset.output_names))):
          if FLAGS.if_discrete_loss:
            outputs_to_num_classes[output] = bin_num
          else:
           outputs_to_num_classes[output] = 1
          outputs_to_indices[output] = idx
      bin_vals = [tf.constant(value=[bin_range[i]], dtype=tf.float32, shape=[1, dataset.bin_nums[i]], name=name) \
              for i, name in enumerate(dataset.output_names[:7])]
      # print outputs_to_num_classes
      # print spaces_to_indices

      samples = input_generator.get(
          dataset,
          clone_batch_size,
          dataset_split=FLAGS.train_split,
          is_training=True,
          model_variant=FLAGS.model_variant)
      inputs_queue = prefetch_queue.prefetch_queue(
          samples, capacity=128 * config.num_clones)

      samples_val = input_generator.get(
          dataset_val,
          clone_batch_size,
          dataset_split=FLAGS.val_split,
          is_training=False,
          model_variant=FLAGS.model_variant)
      inputs_queue_val = prefetch_queue.prefetch_queue(
          samples_val, capacity=128)

    # Create the global step on the device storing the variables.
    with tf.device(config.variables_device()):
      global_step = tf.train.get_or_create_global_step()

      # Define the model and create clones.
      model_fn = _build_deeplab
      model_args = (FLAGS, inputs_queue.dequeue(), outputs_to_num_classes, outputs_to_indices, bin_vals, dataset, True, False)
      clones = model_deploy.create_clones(config, model_fn, args=model_args)

      # Gather update_ops from the first clone. These contain, for example,
      # the updates for the batch_norm variables created by model_fn.
      first_clone_scope = config.clone_scope(0) # clone_0
      update_ops = tf.get_collection(tf.GraphKeys.UPDATE_OPS, first_clone_scope)

    with tf.device('/device:GPU:3'):
        if FLAGS.if_val:
          ## Construct the validation graph; takes one GPU.
          _build_deeplab(FLAGS, inputs_queue_val.dequeue(), outputs_to_num_classes, outputs_to_indices, bin_vals, dataset_val, is_training=False, reuse=True)

    # Gather initial summaries.
    summaries = set(tf.get_collection(tf.GraphKeys.SUMMARIES))

    # Add summaries for images, labels, semantic predictions
    summary_loss_dict = {}
    if FLAGS.save_summaries_images:
      if FLAGS.num_clones > 1:
          pattern_train = first_clone_scope + '/%s:0'
      else:
          pattern_train = '%s:0'
      pattern_val = 'val-%s:0'
      pattern = pattern_val if FLAGS.if_val else pattern_train

      summary_mask = graph.get_tensor_by_name(pattern%'not_ignore_mask_in_loss')
      summary_mask = tf.reshape(summary_mask, [-1, dataset.height, dataset.width, 1])
      summary_mask_float = tf.to_float(summary_mask)
      summaries.add(tf.summary.image('gt/%s' % 'not_ignore_mask', tf.gather(tf.cast(summary_mask_float*255., tf.uint8), [0, 1, 2])))

      summary_image = graph.get_tensor_by_name(pattern%common.IMAGE)
      summaries.add(tf.summary.image('gt/%s' % common.IMAGE, tf.gather(summary_image, [0, 1, 2])))

      summary_image_name = graph.get_tensor_by_name(pattern%common.IMAGE_NAME)
      summaries.add(tf.summary.text('gt/%s' % common.IMAGE_NAME, tf.gather(summary_image_name, [0, 1, 2])))

      summary_vis = graph.get_tensor_by_name(pattern%'vis')
      summaries.add(tf.summary.image('gt/%s' % 'vis', tf.gather(summary_vis, [0, 1, 2])))

      def scale_to_255(tensor, pixel_scaling=None):
          tensor = tf.to_float(tensor)
          if pixel_scaling == None:
              offset_to_zero = tf.reduce_min(tensor)
              scale_to_255 = tf.div(255., tf.reduce_max(tensor - offset_to_zero))
          else:
              offset_to_zero, scale_to_255 = pixel_scaling
          summary_tensor_float = tensor - offset_to_zero
          summary_tensor_float = summary_tensor_float * scale_to_255
          summary_tensor_float = tf.clip_by_value(summary_tensor_float, 0., 255.)
          summary_tensor_uint8 = tf.cast(summary_tensor_float, tf.uint8)
          return summary_tensor_uint8, (offset_to_zero, scale_to_255)

      label_outputs = graph.get_tensor_by_name(pattern%common.LABEL)
      label_id_outputs = graph.get_tensor_by_name(pattern%'label_id')
      logit_outputs = graph.get_tensor_by_name(pattern%'scaled_logits')

      summary_rot_diffs = graph.get_tensor_by_name(pattern%'rot_diffs')
      summaries.add(tf.summary.image('diff_map/%s' % 'rot_diffs', tf.gather(summary_rot_diffs, [0, 1, 2])))

      summary_trans_diffs = graph.get_tensor_by_name(pattern%'trans_diffs')
      summaries.add(tf.summary.image('diff_map/%s' % 'trans_diffs', tf.gather(summary_trans_diffs, [0, 1, 2])))

      shape_id_outputs = graph.get_tensor_by_name(pattern%'shape_id_map')
      shape_id_outputs = tf.where(summary_mask, shape_id_outputs+1, tf.zeros_like(shape_id_outputs))
      summary_shape_id_output_uint8, _ = scale_to_255(shape_id_outputs)
      summaries.add(tf.summary.image('test/shape_id_map', tf.gather(summary_shape_id_output_uint8, [0, 1, 2])))

      shape_id_outputs_gt = graph.get_tensor_by_name(pattern%'shape_id_map_gt')
      shape_id_outputs_gt = tf.where(summary_mask, shape_id_outputs_gt+1, tf.zeros_like(shape_id_outputs))
      summary_shape_id_output_uint8_gt, _ = scale_to_255(shape_id_outputs_gt)
      summaries.add(tf.summary.image('test/shape_id_map_gt', tf.gather(summary_shape_id_output_uint8_gt, [0, 1, 2])))

      shape_id_outputs = graph.get_tensor_by_name(pattern%'shape_id_map_predict')
      summary_shape_id_output = tf.where(summary_mask, shape_id_outputs, tf.zeros_like(shape_id_outputs))
      summary_shape_id_output_uint8, _ = scale_to_255(summary_shape_id_output)
      summaries.add(tf.summary.image('test/shape_id_map_predict', tf.gather(summary_shape_id_output_uint8, [0, 1, 2])))

      shape_id_cls_error_map = graph.get_tensor_by_name(pattern%'shape_id_cls_error_map')
      # summary_shape_id_output = tf.where(summary_mask, shape_id_outputs, tf.zeros_like(shape_id_outputs))
      shape_id_cls_error_map_uint8, _ = scale_to_255(shape_id_cls_error_map)
      summaries.add(tf.summary.image('test/shape_id_cls_error_map', tf.gather(shape_id_cls_error_map_uint8, [0, 1, 2])))

      for output_idx, output in enumerate(dataset.output_names[:7]):
          # # Scale up summary image pixel values for better visualization.
          summary_label_output = tf.gather(label_outputs, [output_idx], axis=3)
          summary_label_output= tf.where(summary_mask, summary_label_output, tf.zeros_like(summary_label_output))
          summary_label_output_uint8, pixel_scaling = scale_to_255(summary_label_output)
          summaries.add(tf.summary.image('output/%s_label' % output, tf.gather(summary_label_output_uint8, [0, 1, 2])))

          summary_logit_output = tf.gather(logit_outputs, [output_idx], axis=3)
          summary_logit_output = tf.where(summary_mask, summary_logit_output, tf.zeros_like(summary_logit_output))
          summary_logit_output_uint8, _ = scale_to_255(summary_logit_output, pixel_scaling)
          summaries.add(tf.summary.image(
              'output/%s_logit' % output, tf.gather(summary_logit_output_uint8, [0, 1, 2])))

          summary_label_id_output = tf.to_float(tf.gather(label_id_outputs, [output_idx], axis=3))
          summary_label_id_output = tf.where(summary_mask, summary_label_id_output+1, tf.zeros_like(summary_label_id_output))
          summary_label_id_output_uint8, _ = scale_to_255(summary_label_id_output)
          summary_label_id_output_uint8 = tf.identity(summary_label_id_output_uint8, 'tttt'+output)
          summaries.add(tf.summary.image(
              'test/%s_label_id' % output, tf.gather(summary_label_id_output_uint8, [0, 1, 2])))

          summary_diff = tf.abs(tf.to_float(summary_label_output_uint8) - tf.to_float(summary_logit_output_uint8))
          summary_diff = tf.where(summary_mask, summary_diff, tf.zeros_like(summary_diff))
          summaries.add(tf.summary.image('output/%s_ldiff' % output, tf.gather(tf.cast(summary_diff, tf.uint8), [0, 1, 2])))

          summary_loss = graph.get_tensor_by_name((pattern%'loss_reg_').replace(':0', '')+output+':0')
          summaries.add(tf.summary.scalar('slice_loss/'+(pattern%'_loss_reg_').replace(':0', '')+output, summary_loss))

          summary_loss = graph.get_tensor_by_name((pattern%'loss_cls_').replace(':0', '')+output+':0')
          summaries.add(tf.summary.scalar('slice_loss/'+(pattern%'_loss_cls_').replace(':0', '')+output, summary_loss))

      for pattern in [pattern_train, pattern_val] if FLAGS.if_val else [pattern_train]:
          for loss_name in ['loss_all', 'loss_all_rot_quat_metric', 'loss_all_rot_quat', 'loss_all_trans_metric',
                  'loss_all_trans', 'loss_cls_ALL', 'loss_all_shape', 'loss_all_shape_id_cls', 'loss_all_shape_id_cls_metric']:
              if pattern == pattern_val:
                summary_loss_avg = graph.get_tensor_by_name(pattern%loss_name)
                # summary_loss_dict['val-'+loss_name] = summary_loss_avg
              else:
                summary_loss_avg = train_utils.get_avg_tensor_from_scopes(FLAGS.num_clones, '%s:0', graph, config, loss_name)
                # summary_loss_dict['train-'+loss_name] = summary_loss_avg
              summaries.add(tf.summary.scalar(('total_loss/'+pattern%loss_name).replace(':0', ''), summary_loss_avg))


    # Build the optimizer based on the device specification.
    with tf.device(config.optimizer_device()):
      learning_rate = train_utils.get_model_learning_rate(
          FLAGS.learning_policy, FLAGS.base_learning_rate,
          FLAGS.learning_rate_decay_step, FLAGS.learning_rate_decay_factor,
          FLAGS.training_number_of_steps, FLAGS.learning_power,
          FLAGS.slow_start_step, FLAGS.slow_start_learning_rate)
      # optimizer = tf.train.MomentumOptimizer(learning_rate, FLAGS.momentum)
      optimizer = tf.train.AdamOptimizer(learning_rate)
      summaries.add(tf.summary.scalar('learning_rate', learning_rate))

    startup_delay_steps = FLAGS.task * FLAGS.startup_delay_steps

    with tf.device(config.variables_device()):
      total_loss, grads_and_vars = model_deploy.optimize_clones(
          clones, optimizer)
      print '------ total_loss', total_loss, tf.get_collection(tf.GraphKeys.LOSSES, first_clone_scope)
      total_loss = tf.check_numerics(total_loss, 'Loss is inf or nan.')
      summaries.add(tf.summary.scalar('total_loss/train', total_loss))

      # Modify the gradients for biases and last layer variables.
      last_layers = model.get_extra_layer_scopes(
          FLAGS.last_layers_contain_logits_only)
      print '////last layers', last_layers

      # Filter trainable variables for last layers ONLY.
      # grads_and_vars = train_utils.filter_gradients(last_layers, grads_and_vars)

      grad_mult = train_utils.get_model_gradient_multipliers(
          last_layers, FLAGS.last_layer_gradient_multiplier)
      if grad_mult:
        grads_and_vars = slim.learning.multiply_gradients(
            grads_and_vars, grad_mult)

      # Create gradient update op.
      grad_updates = optimizer.apply_gradients(
          grads_and_vars, global_step=global_step)
      update_ops.append(grad_updates)
      update_op = tf.group(*update_ops)
      with tf.control_dependencies([update_op]):
        train_tensor = tf.identity(total_loss, name='train_op')

    # Add the summaries from the first clone. These contain the summaries
    # created by model_fn and either optimize_clones() or _gather_clone_loss().
    summaries |= set(
        tf.get_collection(tf.GraphKeys.SUMMARIES, first_clone_scope))

    # Merge all summaries together.
    summary_op = tf.summary.merge(list(summaries))

    # Soft placement allows placing on CPU ops without GPU implementation.
    session_config = tf.ConfigProto(
        allow_soft_placement=True, log_device_placement=False)
    session_config.gpu_options.allow_growth = True

    def train_step_fn(sess, train_op, global_step, train_step_kwargs):
        train_step_fn.step += 1  # or use global_step.eval(session=sess)

        # calc training losses
        loss, should_stop = slim.learning.train_step(sess, train_op, global_step, train_step_kwargs)
        print loss
        # print 'loss: ', loss
        # first_clone_test = graph.get_tensor_by_name(
        #         ('%s/%s:0' % (first_clone_scope, 'shape_map')).strip('/'))
        # test = sess.run(first_clone_test)
        # # print test
        # print 'test: ', test.shape, np.max(test), np.min(test), np.mean(test), test.dtype
        should_stop = 0

        if FLAGS.if_val and train_step_fn.step % FLAGS.val_interval_steps == 0:
            # first_clone_test = graph.get_tensor_by_name('val-loss_all:0')
            # test = sess.run(first_clone_test)
            print '-- Validating...'
            first_clone_test = graph.get_tensor_by_name(
                    ('%s/%s:0' % (first_clone_scope, 'shape_id_cls_error_map')).strip('/'))
            first_clone_test2 = graph.get_tensor_by_name(
                    ('%s/%s:0' % (first_clone_scope, 'shape_id_map_gt')).strip('/'))
                    # 'ttttrow:0')
            test_out, test_out2 = sess.run([first_clone_test, first_clone_test2])
            # test_out = test[:, :, :, 3]
            test_out = test_out[test_out!=0]
            # test_out2 = test2[:, :, :, 3]
            test_out2 = test_out2[test_out2!=0]
            # print test_out
            print 'output: ', test_out.shape, np.max(test_out), np.min(test_out), np.mean(test_out), np.median(test_out), test_out.dtype
            print 'label: ', test_out2.shape, np.max(test_out2), np.min(test_out2), np.mean(test_out2), np.median(test_out2), test_out2.dtype

        # first_clone_label = graph.get_tensor_by_name(
        #         ('%s/%s:0' % (first_clone_scope, common.LABEL)).strip('/')) # clone_0/val-loss:0
        # # first_clone_pose_dict = graph.get_tensor_by_name(
        # #         ('%s/%s:0' % (first_clone_scope, 'pose_dict')).strip('/'))
        # first_clone_logit = graph.get_tensor_by_name(
        #         ('%s/%s:0' % (first_clone_scope, 'scaled_regression')).strip('/'))
        # not_ignore_mask = graph.get_tensor_by_name(
        #         ('%s/%s:0' % (first_clone_scope, 'not_ignore_mask_in_loss')).strip('/'))
        # label, logits, mask = sess.run([first_clone_label, first_clone_logit, not_ignore_mask])
        # mask = np.reshape(mask, (-1, FLAGS.train_crop_size[0], FLAGS.train_crop_size[1], dataset.num_classes))

        # print '... shapes, types, loss', label.shape, label.dtype, logits.shape, logits.dtype, loss
        # print 'mask', mask.shape, np.mean(mask)
        # logits[mask==0.] = 0.
        # print 'logits', logits.shape, np.max(logits), np.min(logits), np.mean(logits), logits.dtype
        # for idx in range(6):
        #     print idx, np.max(label[:, :, :, idx]), np.min(label[:, :, :, idx])
        # label = label[:, :, :, 5]
        # print 'label', label.shape, np.max(label), np.min(label), np.mean(label), label.dtype
        # print pose_dict, pose_dict.shape
        # # print 'training....... logits stats: ', np.max(logits), np.min(logits), np.mean(logits)
        # # label_one_piece = label[0, :, :, 0]
        # # print 'training....... label stats', np.max(label_one_piece), np.min(label_one_piece), np.sum(label_one_piece[label_one_piece!=255.])
        return [loss, should_stop]
    train_step_fn.step = 0


    # trainables = [v.name for v in tf.trainable_variables()]
    # alls =[v.name for v in tf.all_variables()]
    # print '----- Trainables %d: '%len(trainables), trainables
    # print '----- All %d: '%len(alls), alls[:10]
    # print '===== ', len(list(set(trainables) - set(alls)))
    # print '===== ', len(list(set(alls) - set(trainables)))

    if FLAGS.if_print_tensors:
        for op in tf.get_default_graph().get_operations():
            print str(op.name)

    # Start the training.
    slim.learning.train(
        train_tensor,
        train_step_fn=train_step_fn,
        logdir=FLAGS.train_logdir,
        log_every_n_steps=FLAGS.log_steps,
        master=FLAGS.master,
        number_of_steps=FLAGS.training_number_of_steps,
        is_chief=(FLAGS.task == 0),
        session_config=session_config,
        startup_delay_steps=startup_delay_steps,
        init_fn=train_utils.get_model_init_fn(
            FLAGS.restore_logdir,
            FLAGS.tf_initial_checkpoint,
            FLAGS.if_restore,
            FLAGS.initialize_last_layer,
            last_layers,
            ignore_missing_vars=True),
        summary_op=summary_op,
        save_summaries_secs=FLAGS.save_summaries_secs,
        save_interval_secs=FLAGS.save_interval_secs)


if __name__ == '__main__':
  flags.mark_flag_as_required('base_logdir')
  flags.mark_flag_as_required('tf_initial_checkpoint')
  flags.mark_flag_as_required('dataset_dir')
  tf.app.run()
