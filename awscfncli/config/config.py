# -*- encoding: utf-8 -*-

import os
import fnmatch
import logging
import yaml
import six
from collections import OrderedDict

from .schema import validate_config
from .exceptions import ConfigError

CANNED_STACK_POLICIES = {
    'ALLOW_ALL': '{"Statement":[{"Effect":"Allow","Action":"Update:*","Principal":"*","Resource":"*"}]}',
    'ALLOW_MODIFY': '{"Statement":[{"Effect":"Allow","Action":["Update:Modify"],"Principal":"*","Resource":"*"}]}',
    'DENY_DELETE': '{"Statement":[{"Effect":"Allow","NotAction":"Update:Delete","Principal":"*","Resource":"*"}]}',
    'DENY_ALL': '{"Statement":[{"Effect":"Deny","Action":"Update:*","Principal":"*","Resource":"*"}]}',
}


def _normalize_value(v):
    if isinstance(v, bool):
        return 'true' if v else 'false'
    elif isinstance(v, int):
        return str(v)
    else:
        return v


def load_config(filename):
    logging.debug('Loading config "%s"' % filename)
    config = CfnCliConfig()
    config.load(filename)
    return config


class CfnCliConfig(object):
    def __init__(self):
        self._version = None
        self._stages = dict()

    def load(self, filename):
        with open(filename) as fp:
            try:
                config = yaml.safe_load(fp)
            except yaml.MarkedYAMLError as e:
                raise ConfigError(e)
            if config is None:
                config = dict()

        self._basedir = os.path.dirname(filename)
        self._version = self._load_version(config)
        validate_config(config, self._version)
        self._stages = self._load_config(config)

    @property
    def version(self):
        return self._version

    def list_stages(self):
        return self._stages.keys()

    def list_stacks(self, stage_name):
        return self._stages[stage_name].keys()

    def get_stack(self, stage_name, stack_name):
        return self._stages[stage_name][stack_name]

    def search_stacks(self, stage_pattern='*', stack_pattern='*'):
        """Find all stack config matching stage/stack patterns
        """
        result = list()
        for stage_id in self.list_stages():
            if fnmatch.fnmatchcase(stage_id, stage_pattern):
                for stack_id in self.list_stacks(stage_id):
                    if fnmatch.fnmatchcase(stack_id, stack_pattern):
                        stack_config = \
                            self.get_stack(stage_id, stack_id)
                        result.append(stack_config)

        result.sort(key=lambda c: c.stack_order)

        return result

    def _load_version(self, config):
        version = config.get('Version', 1)
        logging.debug('Loading version %s' % version)
        return version

    def _load_config(self, config):

        blueprints = config.get('Blueprints', dict())

        stages = dict()
        for stage_id, stage_config in six.iteritems(config['Stages']):
            logging.debug('Loading stage "%s"' % stage_id)

            stacks = dict()
            for stack_id, stack_config in six.iteritems(stage_config):
                logging.debug('Loading stage "%s" stack "%s"' % (
                    stage_id, stack_id))

                # find blueprint
                blueprint_id = stack_config.pop('Extends', None)
                if blueprint_id:
                    try:
                        blueprint = blueprints[blueprint_id]
                    except KeyError:
                        raise ConfigError(
                            'Blueprint "%s" not found' % blueprint_id)
                else:
                    blueprint = dict()

                # find stack stack_order, default
                stack_order = stack_config.pop('Order', 0)

                config = StackConfig(
                    stage_id, stack_id, stack_order, self._basedir)
                config.update(**blueprint)
                config.update(**stack_config)

                stacks[stack_id] = config

            stages[stage_id] = stacks

        return stages


class StackConfig(object):
    PROPERTIES = dict(
        StackName=(six.string_types, None),
        Profile=(six.string_types, None),
        Region=(six.string_types, None),
        Package=(bool, None),
        ArtifactStore=(six.string_types, None),
        Template=(six.string_types, None),
        Parameters=(dict, None),
        DisableRollback=(bool, None),
        RollbackConfiguration=(dict, None),
        TimeoutInMinutes=(six.integer_types, None),
        NotificationARNs=(six.string_types, None),
        Capabilities=(list, None),
        ResourceTypes=(list, None),
        RoleARN=(six.string_types, None),
        OnFailure=(six.string_types, None),
        StackPolicy=(six.string_types, None),
        Tags=(dict, None),
        ClientRequestToken=(six.string_types, None),
        EnableTerminationProtection=(bool, None),
    )

    def __init__(self, stage_id, stack_id, stack_order, basedir):
        self._stage_id = stage_id
        self._stack_id = stack_id
        self._stack_order = stack_order
        self._basedir = basedir
        self._properties = dict((k, v[1]) for k, v in self.PROPERTIES.items())

    @property
    def stage_id(self):
        return self._stage_id

    @property
    def stack_id(self):
        return self._stack_id

    @property
    def stack_order(self):
        return self._stack_order

    @property
    def properties(self):
        return self._properties

    def update(self, **params):
        for k, v in six.iteritems(self.PROPERTIES):
            if k in params:
                val_type, default_val = v

                if self._properties[k] is None:
                    self._properties[k] = params[k]
                else:
                    if k == 'Capabilities':
                        self._properties[k] = params[k]
                    else:
                        if val_type == list:
                            self._properties[k].extend(params[k])
                        elif val_type == dict:
                            self._properties[k].update(params[k])
                        else:
                            self._properties[k] = params[k]

    def to_boto3_format(self):
        properties = self.properties

        # inject parameters
        StackName = properties['StackName']
        if StackName is None:
            # if StackName is not specified, use the key of
            # stack config as stack name.
            StackName = self.stack_id

        Profile = properties['Profile']
        Region = properties['Region']
        Package = properties['Package']
        ArtifactStore = properties['ArtifactStore']

        # move those are not part of create_stack() call to metadata
        metadata = dict(
            Profile=Profile,
            Region=Region,
            Package=Package,
            ArtifactStore=ArtifactStore,
            Order=self.stack_order
        )

        # magically select template body or template url
        Template = properties['Template']
        if Template.startswith('https') or Template.startswith('http'):
            # s3 template
            TemplateURL, TemplateBody = Template, None
        elif Package:
            # local template with package=on
            TemplateURL = os.path.realpath(
                os.path.join(self._basedir, Template))
            TemplateBody = None
        else:
            # local template
            TemplateURL = None
            with open(os.path.join(self._basedir, Template)) as fp:
                TemplateBody = fp.read()

        # lookup canned policy
        StackPolicy = properties['StackPolicy']
        if StackPolicy is not None:
            try:
                StackPolicyBody = CANNED_STACK_POLICIES[StackPolicy]
            except KeyError:
                raise ConfigError(
                    'Invalid canned policy "%s", valid values are: %s.' % \
                    (StackPolicy, ', '.join(CANNED_STACK_POLICIES.keys())))

        else:
            StackPolicyBody = None

        # Normalize parameter config
        Parameters = properties['Parameters']
        normalized_params = None
        if Parameters and isinstance(Parameters, dict):
            normalized_params = list(
                {
                    'ParameterKey': k,
                    'ParameterValue': _normalize_value(v)
                }
                for k, v in
                six.iteritems(OrderedDict(
                    sorted(six.iteritems(Parameters)))
                )
            )

        # Normalize tag config
        Tags = properties['Tags']
        normalized_tags = None
        if Tags and isinstance(Tags, dict):
            normalized_tags = list(
                {'Key': k, 'Value': v}
                for k, v in
                six.iteritems(OrderedDict(
                    sorted(six.iteritems(Tags)))
                )
            )

        normalized_config = dict(
            Metadata=metadata,
            StackName=StackName,
            TemplateURL=TemplateURL,
            TemplateBody=TemplateBody,
            DisableRollback=properties['DisableRollback'],
            RollbackConfiguration=properties['RollbackConfiguration'],
            TimeoutInMinutes=properties['TimeoutInMinutes'],
            NotificationARNs=properties['NotificationARNs'],
            Capabilities=properties['Capabilities'],
            ResourceTypes=properties['ResourceTypes'],
            RoleARN=properties['RoleARN'],
            OnFailure=properties['OnFailure'],
            StackPolicyBody=StackPolicyBody,
            Parameters=normalized_params,
            Tags=normalized_tags,
            ClientRequestToken=properties['ClientRequestToken'],
            EnableTerminationProtection=properties[
                'EnableTerminationProtection'],
        )

        # drop all None and empty list
        normalized_config = dict(
            (k, v) for k, v in six.iteritems(normalized_config) if
            v is not None)

        return normalized_config
