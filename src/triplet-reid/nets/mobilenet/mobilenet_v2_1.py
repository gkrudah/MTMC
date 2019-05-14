import tensorflow as tf

from nets.mobilenet.mobilenet_v2 import mobilenet
from nets.mobilenet import mobilenet as lib
from tensorflow.contrib import slim


def endpoints(image, is_training):
    if image.get_shape().ndims != 4:
        raise ValueError('Input must be of size [batch, height, width, 3]')

    image = tf.divide(image, 255.0)

    with tf.contrib.slim.arg_scope(training_scope(bn_decay=0.9, weight_decay=0.0)):
        _, endpoints = mobilenet(image, num_classes=1001, is_training=is_training)

    endpoints['model_output'] = endpoints['global_pool'] = tf.reduce_mean(
        endpoints['layer_14'], [1, 2], name='global_pool', keep_dims=False)

    return endpoints, 'MobilenetV2'


def training_scope(**kwargs):
  """Defines MobilenetV2 training scope.
  Usage:
     with tf.contrib.slim.arg_scope(mobilenet_v2.training_scope()):
       logits, endpoints = mobilenet_v2.mobilenet(input_tensor)
  with slim.
  Args:
    **kwargs: Passed to mobilenet.training_scope. The following parameters
    are supported:
      weight_decay- The weight decay to use for regularizing the model.
      stddev-  Standard deviation for initialization, if negative uses xavier.
      dropout_keep_prob- dropout keep probability
      bn_decay- decay for the batch norm moving averages.
  Returns:
    An `arg_scope` to use for the mobilenet v2 model.
  """
  return lib.training_scope(**kwargs)