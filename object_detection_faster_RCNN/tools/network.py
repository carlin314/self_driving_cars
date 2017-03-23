import tensorflow as tf
import roi_pooling_layer.roi_pooling_op as roi_pool_op
from roi_pooling_layer.roi_pooling_op import (
    roi_pool,
    roi_pool_grad
)
from rpn_msr.proposal_layer_tf import proposal_layer as proposal_layer_py
from rpn_msr.anchor_target_layer_tf import anchor_target_layer as anchor_target_layer_py
from rpn_msr.proposal_target_layer_tf import proposal_target_layer as proposal_target_layer_py

DEFAULT_PADDING = 'SAME'

def layer(op):
    def layer_decorated(self, *args, **kwargs):
        name = kwargs.setdefault('name', self.get_unique_name(op.__name__))
        if len(self.inputs) == 0:
            raise RuntimeError('No input variables found for layer %s.' % name)
        elif len(self.inputs) == 1:
            layer_input = self.inputs[0]
        else:
            layer_input = list(self.inputs)

        layer_output = op(self, layer_input, *args, **kwargs)
        self.layers[name] = layer_output
        self.feed(layer_output)
        return self
    return layer_decorated

class Network(object):
    def feed(self, *args):
        print("feed")
        assert len(args) != 0
        self.inputs = []
        for layer in args:
            if isinstance(layer, basestring):
                try:
                    layer = self.layers[layer]
                except KeyError:
                    raise KeyError('Unkown layer name fed: %s' % layer)
            self.inputs.append(layer)
        return self

    def validate_padding(self, padding):
        assert padding in ('SAME', 'VALID')

    def make_var(self, name, shape, initializer=None, trainable=True):
        return tf.get_variable(name, shape, initializer=initializer, trainable=trainable)

    @layer
    def relu(self, input, name):
        return tf.nn.relu(input, name=name)

    @layer
    def max_pool(self, input, k_h, k_w, s_h, s_w, name, padding=DEFAULT_PADDING):
        print("max_pool")
        self.validate_padding(padding)
        return tf.nn.max_pool(
            input,
            ksize=[1, k_h, k_w, 1],
            strides=[1, s_h, s_w, 1],
            padding=padding,
            name=name
        )

    @layer
    def avg_pool(self, input, k_w, k_h, s_h, s_w, name, padding=DEFAULT_PADDING):
        self.validate_padding(padding)
        return tf.nn.avg_pool(
            input,
            ksize=[1, k_h, k_w, 1],
            strides=[1, s_h, s_w, 1],
            padding=padding,
            name=name
        )

    @layer
    def conv(
            self,
            input,
            k_h,
            k_w,
            c_o,
            s_h,
            s_w,
            name,
            relu=True,
            padding=DEFAULT_PADDING,
            group=1,
            trainable=True
    ):
        print("conv")
        self.validate_padding(padding)
        c_i = input.get_shape()[-1]
        assert c_i%group == 0
        assert c_o%group == 0
        convolve = lambda i, k: tf.nn.conv2d(i, k, [1, s_h, s_w, 1], padding=padding)
        with tf.variable_scope(name) as scope:
            init_weights = tf.truncated_normal_initializer(0.0, stddev=0.01)
            init_biases = tf.constant_initializer(0.0)
            kernel = self.make_var('weights', [k_h, k_w, c_i/group, c_o], init_weights, trainable)
            biases = self.make_var('biases', [c_o], init_biases, trainable)

            if group == 1:
                conv = convolve(input, kernel)
            else:
                input_groups = tf.split(3, group, input)
                kernel_groups = tf.split(3, group, kernel)
                output_groups = [convolve(i, k) for i,k in zip(input_groups, kernel_groups)]
                conv = tf.concat(3, output_groups)
            if relu:
                bias = tf.nn.bias_add(conv, biases)
                return tf.nn.relu(bias, name=scope.name)
            return tf.nn.bias_add(conv, biases, name=scope.name)

    @layer
    def roi_pool(self, input, pooled_height, pooled_width, spatial_scale, name):
        print("roi_pool")

        if isinstance(input[0], tuple):
            input[0] = input[0][0]

        if isinstance(input[1], tuple):
            input[1] = input[1][0]

        result = roi_pool_op.roi_pool(
            input[0],
            input[1],
            pooled_height,
            pooled_width,
            spatial_scale,
            name=name
        )[0]
        return result

    @layer
    def proposal_layer(self, input, _feat_stride, anchor_scales, cfg_key, name):
        print("proposal_layer")
        if isinstance(input[0], tuple):
            input[0] = input[0][0]

        return tf.reshape(tf.py_func(proposal_layer_py, [input[0], input[1], input[2], cfg_key, _feat_stride, anchor_scales], [tf.float32]), [-1, 5], name=name)

    @layer
    def anchor_target_layer(self, input, _feat_stride, anchor_scales, name):
        if isinstance(input[0], tuple):
            input[0] = input[0][0]

        with tf.variable_scope(name) as scope:

            rpn_labels, rpn_bbox_targets, rpn_bbox_inside_weights, rpn_bbox_outside_weights = ty.py_func(anchor_target_layer_py, [input[0], input[1], input[2], input[3], _feat_stride, anchor_scales], [tf.float32, tf.float32, tf.float32, tf.float32])

            rpn_labels = tf.convert_to_tensor(tf.cast(rpn_labels, tf.int32), name='rpn_labels')
            rpn_bbox_targets = tf.convert_to_tensor(rpn_bbox_targets, name='rpn_bbox_targets')
            rpn_bbox_inside_weights = tf.convert_to_tensor(rpn_bbox_inside_weights, name='rpn_bbox_inside_weights')
            rpn_bbox_outside_weights = tf.convert_to_tensor(rpn_bbox_outside_weights, name='rpn_bbox_outside_weights')

            return rpn_labels, rpn_bbox_targets, rpn_bbox_inside_weights, rpn_bbox_outside_weights

    @layer
    def proposal_target_layer(self, input, classes, name):
        if isinstance(input[0], tuple):
            input[0] = input[0][0]

        with tf.variable_scope(name) as scope:

            rois, labels, bbox_targets, bbox_inside_weights, bbox_outside_weights = tf.py_func(proposal_target_layer_py, [input[0], input[1], classes], [tf.float32, tf.float32, tf.float32, tf.float32, tf.float32])

            rois = tf.reshape(rois, [-1, 5], name='rois')
            labels = tf.convert_to_tensor(tf.cast(labels, tf.int32), name='labels')
            bbox_targets = tf.convert_to_tensor(bbox_targets, name='bbox_targets')
            bbox_inside_weights = tf.convert_to_tensor(bbox_inside_weights, name='bbox_inside_weights')
            bbox_outside_weights = tf.convert_to_tensor(bbox_outside_weights, name='bbox_outside_weights')

            return rois, labels, bbox_targets, bbox_inside_weights, bbox_outside_weights

    @layer
    def fc(self, input, num_out, name, relu=True, trainable=True):
        print("fc with name %s" % name)
        with tf.variable_scope(name) as scope:

            if isinstance(input, tuple):
                input = input[0]

            input_shape = input.get_shape()
            if input_shape.ndims == 4:
                dim = 1
                for d in input_shape[1:].as_list():
                    dim *= d
                feed_in = tf.reshape(tf.transpose(input, [0, 3, 1, 2]), [-1, dim])
            else:
                feed_in, dim = (input, int(input_shape[-1]))

            if name == 'bbox_pred':
                init_weights = tf.truncated_normal_initializer(0.0, stddev=0.001)
                init_biases = tf.constant_initializer(0.0)
            else:
                init_weights = tf.truncated_normal_initializer(0.0, stddev=0.01)
                init_biases = tf.constant_initializer(0.0)

            weights = self.make_var('weights', [dim, num_out], init_weights, trainable)
            biases = self.make_var('biases', [num_out], init_biases, trainable)

            op = tf.nn.relu_layer if relu else tf.nn.xw_plus_b
            fc = op(feed_in, weights, biases, name=scope.name)
            return fc

    @layer
    def softmax(self, input, name):
        print("softmax")
        input_shape = tf.shape(input)
        if name == 'rpn_cls_prob':
            return tf.reshape(tf.nn.softmax(tf.reshape(input, [-1, input_shape[3]])), [-1, input_shape[1], input_shape[2], input_shape[3]], name=name)
        else:
            return tf.nn.softmax(input, name=name)

    @layer
    def dropout(self, input, keep_prob, name):
        return tf.nn.dropout(input, keep_prob, name=name)

    def get_unique_name(self, prefix):
        id = sum(t.startswith(prefix) for t,_ in self.layers.items())+1
        return '%s_%d'%(prefix, id)

    @layer
    def reshape_layer(self, input, d, name):
        input_shape = tf.shape(input)
        if name == 'rpn_cls_prob_reshape':
            return tf.transpose(tf.reshape(tf.transpose(input, [0, 3, 1, 2]), [input_shape[0],
                                                                               int(d), tf.cast(
                    tf.cast(input_shape[1], tf.float32) / tf.cast(d, tf.float32) * tf.cast(input_shape[3], tf.float32),
                    tf.int32), input_shape[2]]), [0, 2, 3, 1], name=name)
        else:
            return tf.transpose(tf.reshape(tf.transpose(input, [0, 3, 1, 2]), [input_shape[0],
                                                                               int(d), tf.cast(
                    tf.cast(input_shape[1], tf.float32) * (
                    tf.cast(input_shape[3], tf.float32) / tf.cast(d, tf.float32)), tf.int32), input_shape[2]]),
                                [0, 2, 3, 1], name=name)