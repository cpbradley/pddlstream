from __future__ import print_function

import os
import pickle

from collections import Counter, namedtuple

from pddlstream.language.constants import is_plan
from pddlstream.language.object import Object
from pddlstream.utils import INF, read_pickle, ensure_dir, write_pickle, get_python_version

LOAD_STATISTICS = True
SAVE_STATISTICS = True

import multiprocessing
DATA_DIR = 'statistics/py{:d}/'
DEFAULT_SEARCH_OVERHEAD = 1e2 # TODO: update this over time
EPSILON = 1e-6
# Can also include the overhead to process skeletons

Stats = namedtuple('Stats', ['p_success', 'overhead'])

# TODO: ability to "burn in" streams by sampling artificially to get better estimates

def safe_ratio(numerator, denominator, undefined=None):
    if denominator == 0:
        return undefined
    return float(numerator) / denominator

def geometric_cost(cost, p):
    return safe_ratio(cost, p, undefined=INF)

def check_effort(effort, max_effort):
    if max_effort is None:
        return True
    return effort < max_effort # Exclusive

def compute_plan_effort(stream_plan, **kwargs):
    # TODO: compute effort in the delete relaxation way
    if not is_plan(stream_plan):
        return INF
    if not stream_plan:
        return 0
    return sum(result.get_effort(**kwargs) for result in stream_plan)

##################################################

# TODO: write to a "local" folder containing temp, data2, data3, visualizations

def get_data_path(stream_name, data_gen_dir=None, instances=False):
    if data_gen_dir:
        data_dir = data_gen_dir
    else:
        # TODO: for output data, using process id is not good enough because we can't recover it later
        data_dir = f"statistics/py3.10/data-{str(multiprocessing.current_process().pid)}/"
    if instances:
        file_name = '{}-instances.pkl'.format(stream_name)
    else:
        file_name = '{}.pkl'.format(stream_name)
    return os.path.join(data_dir, file_name)

def load_data(pddl_name, data_gen_dir=None, instances=False):
    if not LOAD_STATISTICS:
        return {}
    filename = get_data_path(pddl_name, data_gen_dir, instances=instances)
    if not os.path.exists(filename):
        return {}
    #try:
    data = read_pickle(filename) # TODO: try/except
    #except pickle.UnpicklingError:
    #return {}
    #print('Loaded:', filename)
    return data

def load_stream_statistics(externals):
    if not externals:
        return
    pddl_name = externals[0].pddl_name # TODO: ensure the same
    # TODO: fresh restart flag
    data = load_data(pddl_name)
    for external in externals:
        if external.name in data:
            external.load_statistics(data[external.name])

##################################################

def dump_online_statistics(externals):
    print('\nLocal External Statistics')
    overall_calls = 0
    overall_overhead = 0
    for external in externals:
        external.dump_online()
        overall_calls += external.online_calls
        overall_overhead += external.online_overhead
    print('Overall calls: {} | Overall overhead: {:.3f}'.format(overall_calls, overall_overhead))

def dump_total_statistics(externals):
    print('\nTotal External Statistics')
    for external in externals:
        external.dump_total()
        # , external.get_effort()) #, data[external.name])

##################################################

def collate_instance_data(external):
    out = []
    for instance in external.instances.values():
        # print(instance)
        # print(instance.results_history)
        # print(instance.num_failures)
        for execution in instance.results_history:
            if not execution:
                continue
            # assert False
            datum = {}
            # for ob in instance.input_objects:
            #     print(type(ob))
            #     if isinstance(ob, Object):
            #         print(f'Value: {ob.value}')
            #         print(f'Value Type: {type(ob.value)}')
            #         print(f'Ob: {ob}')
            datum['inputs'] = [obj.value if isinstance(obj, Object) else obj for obj in instance.input_objects]
            if 'fluent_facts' in dir(instance):
                datum['fluents'] = [obj.value if isinstance(obj, Object) else obj for obj in instance.fluent_facts]
            else:
                datum['fluents'] = []
            if 'output_objects' in dir(execution[0]):
                datum['outputs'] = [obj.value if isinstance(obj, Object) else obj for obj in execution[0].output_objects]
            else:
                datum['outputs'] = []
            datum['outcome'] = 1
            datum['costs'] = [execution[0].success_cost, 0.0]
            datum['motion_cost'] = [0.0]
            datum['label'] = [datum['outcome']] + datum['costs']
            out.append(datum)

        for failure_cost in instance.failure_costs:
            datum = {}
            datum['inputs'] = [obj.value if isinstance(obj, Object) else obj for obj in instance.input_objects]
            if 'fluent_facts' in dir(instance):
                datum['fluents'] = [obj.value if isinstance(obj, Object) else obj for obj in instance.fluent_facts]
            else:
                datum['fluents'] = []
            datum['outputs'] = []
            datum['outcome'] = 0
            datum['costs'] = [0.0, failure_cost]
            datum['motion_cost'] = [0.0]
            datum['label'] = [datum['outcome']] + datum['costs']
            out.append(datum)
    return out

def merge_data(external, previous_data):
    # TODO: compute distribution of successes given feasible
    # TODO: can estimate probability of success given feasible
    # TODO: single tail hypothesis testing (probability that came from this distribution)
    distribution = []
    for instance in external.instances.values():
        if instance.results_history:
            # attempts = len(instance.results_history)
            # successes = sum(map(bool, instance.results_history))
            # print(instance, successes, attempts)
            # TODO: also first attempt, first success
            last_success = -1
            for i, results in enumerate(instance.results_history):
                if results:
                    distribution.append(i - last_success)
                    # successful = (0 <= last_success)
                    last_success = i
    combined_distribution = previous_data.get('distribution', []) + distribution
    # print(external, distribution)
    # print(external, Counter(combined_distribution))
    # TODO: count num failures as well
    # Alternatively, keep metrics on the lower bound and use somehow
    # Could assume that it is some other distribution beyond that point
    return {
        'calls': external.total_calls,
        'overhead': external.total_overhead,
        'successes': external.total_successes,
        'distribution': combined_distribution,
    }
    # TODO: make an instance method

def write_external_statistics(externals, verbose, data_gen_dir=None):
    if not externals:
        return
    if verbose:
        #dump_online_statistics(externals)
        dump_total_statistics(externals)
    pddl_name = externals[0].pddl_name # TODO: ensure the same
    previous_data = load_data(pddl_name)
    previous_instances_data = load_data(pddl_name, data_gen_dir, instances=True)
    data = {}
    instances_data = {}
    for external in externals:
        if not hasattr(external, 'instances'):
            continue # TODO: SynthesizerStreams
        #total_calls = 0 # TODO: compute these values
        previous_statistics = previous_data.get(external.name, {})
        data[external.name] = merge_data(external, previous_statistics)
        instances_data[external.name] = collate_instance_data(external)
    # instances_data.update(previous_instances_data)
    for external_name in previous_instances_data:
        if external_name in instances_data:
            instances_data[external_name].extend(previous_instances_data[external_name])
        else:
            instances_data[external_name] = previous_instances_data[external_name]
    

    if not SAVE_STATISTICS:
        return
    filename = get_data_path(pddl_name)
    instances_filename = get_data_path(pddl_name, data_gen_dir, instances=True)
    ensure_dir(filename)
    ensure_dir(instances_filename)
    write_pickle(filename, data)
    write_pickle(instances_filename, instances_data)
    if verbose:
        # import sys
        # print(sys.getsizeof(instances_data)
        print('Wrote:', filename)
        print('Wrote:', instances_filename)
        # print(instances_data)

def write_stream_statistics(externals, verbose):
    # TODO: estimate conditional to affecting history on skeleton
    # TODO: estimate conditional to first & attempt and success
    # TODO: relate to success for the full future plan
    # TODO: Maximum Likelihood Exponential - average (biased in general)
    if not externals:
        return
    if verbose:
        #dump_online_statistics(externals)
        dump_total_statistics(externals)
    pddl_name = externals[0].pddl_name # TODO: ensure the same
    previous_data = load_data(pddl_name)
    data = {}
    for external in externals:
        if not hasattr(external, 'instances'):
            continue # TODO: SynthesizerStreams
        #total_calls = 0 # TODO: compute these values
        previous_statistics = previous_data.get(external.name, {})
        data[external.name] = merge_data(external, previous_statistics)

    if not SAVE_STATISTICS:
        return
    filename = get_data_path(pddl_name)
    ensure_dir(filename)
    write_pickle(filename, data)
    if verbose:
        print('Wrote:', filename)

##################################################

def hash_object(evaluations, obj):
    # TODO: hash an object by the DAG of streams that produced it
    # Use this to more finely estimate the parameters of a stream
    # Can marginalize over conditional information to recover the same overall statistics
    # Can also apply this directly to domain facts
    raise NotImplementedError()

##################################################

class PerformanceInfo(object):
    def __init__(self, p_success=1-EPSILON, overhead=EPSILON, effort=None, estimate=False):
        # TODO: make info just a dict
        self.estimate = estimate
        if self.estimate:
            p_success = overhead = effort = None
        if p_success is not None:
            assert 0. <= p_success <= 1.
        if overhead is not None:
            assert 0. <= overhead
        #if effort is not None:
        #    assert 0 <= effort
        self.p_success = p_success
        self.overhead = overhead
        self.effort = effort
    def __repr__(self):
        return '{}{}'.format(self.__class__.__name__, repr(self.__dict__))

class Performance(object):
    def __init__(self, name, info):
        self.name = name.lower()
        self.info = info
        self.initial_calls = 0
        self.initial_overhead = 0.
        self.initial_successes = 0
        # TODO: online learning vs offline learning
        self.online_calls = 0
        self.online_overhead = 0.
        self.online_successes = 0
    @property
    def total_calls(self):
        return self.initial_calls + self.online_calls
    @property
    def total_overhead(self):
        return self.initial_overhead + self.online_overhead
    @property
    def total_successes(self):
        return self.initial_successes + self.online_successes
    def load_statistics(self, statistics):
        self.initial_calls = statistics['calls']
        self.initial_overhead = statistics['overhead']
        self.initial_successes = statistics['successes']
    def update_statistics(self, overhead, success):
        self.online_calls += 1
        self.online_overhead += overhead
        self.online_successes += success
    def _estimate_p_success(self, reg_p_success=1., reg_calls=1):
        # TODO: use prior from info instead?
        return safe_ratio(self.total_successes + reg_p_success * reg_calls,
                          self.total_calls + reg_calls,
                          undefined=reg_p_success)
    def _estimate_overhead(self, reg_overhead=1e-6, reg_calls=1):
        # TODO: use prior from info instead?
        return safe_ratio(self.total_overhead + reg_overhead * reg_calls,
                          self.total_calls + reg_calls,
                          undefined=reg_overhead)
    def get_p_success(self):
        # TODO: could precompute and store
        if self.info.p_success is None:
            return self._estimate_p_success()
        return self.info.p_success
    def get_overhead(self):
        if self.info.overhead is None:
            return self._estimate_overhead()
        return self.info.overhead
    def could_succeed(self):
        return self.get_p_success() > 0
    def _estimate_effort(self, search_overhead=DEFAULT_SEARCH_OVERHEAD):
        p_success = self.get_p_success()
        return geometric_cost(self.get_overhead(), p_success) + \
               (1 - p_success) * geometric_cost(search_overhead, p_success)
    def get_effort(self, **kwargs):
        if self.info.effort is None:
            return self._estimate_effort(**kwargs)
        elif callable(self.info.effort):
            return 0  # This really is a bound on the effort
        return self.info.effort
    def get_statistics(self, negate=False): # negate=True is for the "worst-case" ordering
        sign = -1 if negate else +1
        return Stats(p_success=self.get_p_success(), overhead=sign * self.get_overhead())
    def dump_total(self):
        # for instance in self.instances:
        #     print(instance)
        #     print(type(self))
        #     if type(instance[0]) == tuple:
        #         inst = self.get_instance(*instance)
        #     else:
        #         inst = self.get_instance(instance)
        #     if inst.num_failures > 0:
        #         print(f'Num Failures: {inst.num_failures}')
        #         print(f'Num Successes: {inst.num_successes}')
        #     print(f'Instance: {instance}')
        #     print(f'Instance: {inst}')
        #     print(f'Results History: {inst.results_history}')
        #     print(f'History: {inst.history}')
        #     if len(inst.results_history) > 0:
        #         if len(inst.results_history[0]) > 0:
        #             print(vars(inst.results_history[0][0]))
        #     # print(f'Result: {inst.previous_outputs}')
        print('External: {} | n: {:d} | p_success: {:.3f} | overhead: {:.3f}'.format(
            self.name, self.total_calls, self._estimate_p_success(), self._estimate_overhead()))
    def dump_online(self):
        if not self.online_calls:
            return
        print('External: {} | n: {:d} | p_success: {:.3f} | mean overhead: {:.3f} | overhead: {:.3f}'.format(
            self.name, self.online_calls,
            safe_ratio(self.online_successes, self.online_calls),
            safe_ratio(self.online_overhead, self.online_calls),
            self.online_overhead))

##################################################

# TODO: cannot easily do Bayesian hypothesis testing because might never receive ground truth when empty
# In some cases, the stream does finish though

# Estimate probability that will generate result
# Need to produce belief that has additional samples
# P(Success | Samples) = estimated parameter
# P(Success | ~Samples) = 0
# T(Samples | ~Samples) = 0
# T(~Samples | Samples) = 1-p

# TODO: estimate a parameter conditioned on successful streams?
# Need a transition fn as well because generating a sample might change state
# Problem with estimating prior. Don't always have data on failed streams

# Goal: estimate P(Success | History)
# P(Success | History) = P(Success | Samples) * P(Samples | History)

# Previously in Instance
# def get_belief(self):
#     #return 1.
#     #prior = self.external.prior
#     prior = 1. - 1e-2
#     n = self.num_calls
#     p_obs_given_state = self.external.get_p_success()
#     p_state = prior
#     for i in range(n):
#         p_nobs_and_state = (1-p_obs_given_state)*p_state
#         p_nobs_and_nstate = (1-p_state)
#         p_nobs = p_nobs_and_state + p_nobs_and_nstate
#         p_state = p_nobs_and_state/p_nobs
#     return p_state

# def update_belief(self, success):
#     # Belief that remaining sequence is non-empty
#     # Belief only degrades in this case
#     nonempty = 0.9
#     p_success_nonempty = 0.5
#     if success:
#         p_success = p_success_nonempty*nonempty
#     else:
#         p_success = (1-p_success_nonempty)*nonempty + (1-nonempty)

#def get_p_success(self):
    #p_success_belief = self.external.get_p_success()
    #belief = self.get_belief()
    #return p_success_belief*belief
    # TODO: use the external as a prior
    # TODO: Bayesian estimation of likelihood that has result
    # Model hidden state of whether has values or if will produce values?
    # TODO: direct estimation of different buckets in which it will finish
    # TODO: we have samples from the CDF or something

#def get_p_success(self):
#    return self.external.get_p_success()
#
#def get_overhead(self):
#    return self.external.get_overhead()
