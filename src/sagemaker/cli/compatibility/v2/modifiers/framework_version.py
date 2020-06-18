# Copyright 2020 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"). You
# may not use this file except in compliance with the License. A copy of
# the License is located at
#
#     http://aws.amazon.com/apache2.0/
#
# or in the "license" file accompanying this file. This file is
# distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF
# ANY KIND, either express or implied. See the License for the specific
# language governing permissions and limitations under the License.
"""A class to ensure that ``framework_version`` is defined when constructing framework classes."""
from __future__ import absolute_import

import ast

from sagemaker.cli.compatibility.v2.modifiers.modifier import Modifier

FRAMEWORK_ARG = "framework_version"
PY_ARG = "py_version"

FRAMEWORK_DEFAULTS = {
    "Chainer": "4.1.0",
    "MXNet": "1.2.0",
    "PyTorch": "0.4.0",
    "SKLearn": "0.20.0",
    "TensorFlow": "1.11.0",
}

FRAMEWORK_CLASSES = list(FRAMEWORK_DEFAULTS.keys())
MODEL_CLASSES = ["{}Model".format(fw) for fw in FRAMEWORK_CLASSES]

# TODO: check for sagemaker.tensorflow.serving.Model
FRAMEWORK_MODULES = [fw.lower() for fw in FRAMEWORK_CLASSES]
FRAMEWORK_SUBMODULES = ("model", "estimator")


class FrameworkVersionEnforcer(Modifier):
    """A class to ensure that ``framework_version`` is defined when
    instantiating a framework estimator or model.
    """

    def node_should_be_modified(self, node):
        """Checks if the ast.Call node instantiates a framework estimator or model,
        but doesn't specify the ``framework_version`` and ``py_version`` parameter,
        as appropriate.

        This looks for the following formats:

        - ``TensorFlow``
        - ``sagemaker.tensorflow.TensorFlow``

        where "TensorFlow" can be Chainer, MXNet, PyTorch, SKLearn, or TensorFlow.

        Args:
            node (ast.Call): a node that represents a function call. For more,
                see https://docs.python.org/3/library/ast.html#abstract-grammar.

        Returns:
            bool: If the ``ast.Call`` is instantiating a framework class that
                should specify ``framework_version``, but doesn't.
        """
        if _is_named_constructor(node, FRAMEWORK_CLASSES):
            return _version_args_needed(node, "image_name")

        if _is_named_constructor(node, MODEL_CLASSES):
            return _version_args_needed(node, "image")

        return False

    def modify_node(self, node):
        """Modifies the ``ast.Call`` node's keywords to include ``framework_version``.

        The ``framework_version`` value is determined by the framework:

        - Chainer: "4.1.0"
        - MXNet: "1.2.0"
        - PyTorch: "0.4.0"
        - SKLearn: "0.20.0"
        - TensorFlow: "1.11.0"

        The ``py_version`` value is determined by the framework, framework_version, and if it is a
        model, whether the model accepts a py_version

        Args:
            node (ast.Call): a node that represents the constructor of a framework class.
        """
        framework, is_model = _framework_from_node(node)

        # if framework_version is not supplied, get default and append keyword
        framework_version = _arg_value(node, FRAMEWORK_ARG)
        if framework_version is None:
            framework_version = FRAMEWORK_DEFAULTS[framework]
            node.keywords.append(ast.keyword(arg=FRAMEWORK_ARG, value=ast.Str(s=framework_version)))

        # if py_version is not supplied, get a conditional default, and if not None, append keyword
        py_version = _arg_value(node, PY_ARG)
        if py_version is None:
            py_version = _py_version_defaults(framework, framework_version, is_model)
            if py_version:
                node.keywords.append(ast.keyword(arg=PY_ARG, value=ast.Str(s=py_version)))


def _py_version_defaults(framework, framework_version, is_model=False):
    """Gets the py_version required for the framework_version and if it's a model

    Args:
        framework (str): name of the framework
        framework_version (str): version of the framework
        is_model (bool): whether it is a constructor for a model or not

    Returns:
        str: the default py version, as appropriate. None if no default py_version
    """
    if framework in ("Chainer", "PyTorch"):
        return "py3"
    if framework == "SKLearn" and not is_model:
        return "py3"
    if framework == "MXNet":
        return "py2"
    if framework == "TensorFlow" and not is_model:
        return _tf_py_version_default(framework_version)
    return None


def _tf_py_version_default(framework_version):
    """Gets the py_version default based on framework_version for TensorFlow."""
    if not framework_version:
        return "py2"
    version = [int(s) for s in framework_version.split(".")]
    if version < [1, 12]:
        return "py2"
    if version < [2, 2]:
        return "py3"
    return "py37"


def _framework_from_node(node):
    """Retrieves the framework class name based on the function call, and if it was a model

    Args:
        node (ast.Call): a node that represents the constructor of a framework class.
            This can represent either <Framework> or sagemaker.<framework>.<Framework>.

    Returns:
        str, bool: the (capitalized) framework class name, and if it is a model class
    """
    if isinstance(node.func, ast.Name):
        framework = node.func.id
    elif isinstance(node.func, ast.Attribute):
        framework = node.func.attr
    else:
        framework = ""

    is_model = framework.endswith("Model")
    if is_model:
        framework = framework[: framework.find("Model")]

    return framework, is_model


def _is_named_constructor(node, names):
    """Checks if the ``ast.Call`` node represents a call to particular named constructors.

    Forms that qualify are either <Framework> or sagemaker.<framework>.<Framework>
    where <Framework> belongs to the list of names passed in.
    """
    # Check for call from particular names of constructors
    if isinstance(node.func, ast.Name):
        return node.func.id in names

    # Check for something.that.ends.with.<framework>.<Framework> call for Framework in names
    if not (isinstance(node.func, ast.Attribute) and node.func.attr in names):
        return False

    # Check for sagemaker.<frameworks>.<estimator/model>.<Framework> call
    if isinstance(node.func.value, ast.Attribute) and node.func.value.attr in FRAMEWORK_SUBMODULES:
        return _is_in_framework_module(node.func.value)

    # Check for sagemaker.<framework>.<Framework> call
    return _is_in_framework_module(node.func)


def _is_in_framework_module(node):
    """Checks if node is an ``ast.Attribute`` representing a ``sagemaker.<framework>`` module."""
    return (
        isinstance(node.value, ast.Attribute)
        and node.value.attr in FRAMEWORK_MODULES
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "sagemaker"
    )


def _version_args_needed(node, image_arg):
    """Determines if image_arg or version_arg was supplied

    Applies similar logic as ``validate_version_or_image_args``
    """
    # if image_arg is present, no need to supply version arguments
    image_name = _arg_value(node, image_arg)
    if image_name:
        return False

    # if framework_version is None, need args
    framework_version = _arg_value(node, FRAMEWORK_ARG)
    if framework_version is None:
        return True

    # check if we expect py_version and we don't get it -- framework and model dependent
    framework, is_model = _framework_from_node(node)
    expecting_py_version = _py_version_defaults(framework, framework_version, is_model)
    if expecting_py_version:
        py_version = _arg_value(node, PY_ARG)
        return py_version is None

    return False


def _arg_value(node, arg):
    """Gets the value associated with the arg keyword, if present"""
    for kw in node.keywords:
        if kw.arg == arg and kw.value:
            return kw.value.s
    return None