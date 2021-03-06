import numpy as np
import pandas as pd

import gym
from gym import spaces
from gym.utils import seeding

import assets.library


class energy_py(gym.Env):

    def __init__(self, episode_length, lag):
        self.lag = lag
        self.verbose = 0
        self.ts = self.load_data(episode_length)
        self.state_models = [
            {'Name': 'Settlement period', 'Min': 0, 'Max': 48},
            {'Name': 'HGH demand', 'Min': 0, 'Max': 30},
            {'Name': 'LGH demand', 'Min': 0, 'Max': 20},
            {'Name': 'Cooling demand', 'Min': 0, 'Max': 10},
            {'Name': 'Electrical demand', 'Min': 0, 'Max': 20},
            {'Name': 'Ambient temperature', 'Min': 0, 'Max': 30},
            {'Name': 'Gas price', 'Min': 15, 'Max': 25},
            {'Name': 'Import electricity price', 'Min': -200, 'Max': 1600},
            {'Name': 'Export electricity price', 'Min': -200, 'Max': 1600}]

        self.asset_models = [
            assets.library.gas_engine(size=25, name='GT 1'),
            assets.library.gas_engine(size=25, name='GT 2'),
            assets.library.gas_engine(size=25, name='GT 3')]

        self.state_names = [d['Name'] for d in self.state_models]
        self.action_names = [var['Name']
                             for asset in self.asset_models
                             for var in asset.variables]

        self.s_mins, self.s_maxs = self.state_mins_maxs()
        self.a_mins, self.a_maxs = self.asset_mins_maxs()
        self.mins = np.append(self.s_mins, self.a_mins)
        self.maxs = np.append(self.s_maxs, self.a_maxs)

        self.seed()
        self.state = self.reset()

    """""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
                                Open AI methods
    """""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

    def _seed(self, seed=None):  # taken straight from cartpole
        self.np_random, seed = seeding.np_random(seed)
        return [seed]

    def _reset(self):
        self.steps = int(0)
        self.state = self.ts.iloc[0, 1:].values  # visible state
        self.info = []
        self.done = False
        [asset.reset() for asset in self.asset_models]
        self.last_actions = [var['Current']
                             for asset in self.asset_models
                             for var in asset.variables]

        self.observation_space = self.create_obs_space()
        self.action_space, self.lows, self.highs = self.create_action_space(self.last_actions)
        return self.state

    def _step(self, action):
        true_state = self.ts.iloc[self.steps + self.lag, 1:]

        # take actions
        count = 0
        for asset in self.asset_models:
            for var in asset.variables:
                var['Current'] = action[count]
                count += 1
            asset.update()

        # sum of energy inputs/outputs for all assets
        total_gas_burned = sum([asset.gas_burnt for asset in self.asset_models])
        total_HGH_gen = sum([asset.HG_heat_output for asset in self.asset_models])
        total_LGH_gen = sum([asset.LG_heat_output for asset in self.asset_models])
        total_COOL_gen = sum([asset.cooling_output for asset in self.asset_models])
        total_elect_gen = sum([asset.power_output for asset in self.asset_models])

        # energy demands
        elect_dem = true_state['Electrical']
        HGH_dem = true_state['HGH']
        LGH_dem = true_state['LGH']
        COOL_dem = true_state['Cooling']

        # energy balances
        HGH_bal = HGH_dem - total_HGH_gen
        LGH_bal = LGH_dem - total_LGH_gen
        COOL_bal = COOL_dem - total_COOL_gen

        # backup gas boiler to pick up excess load
        backup_blr = max(0, HGH_bal) + max(0, LGH_bal)
        gas_burned = total_gas_burned + (backup_blr / 0.8)

        # backup electric chiller for cooling load
        backup_chiller = max(0, COOL_bal)
        backup_chiller_elect = backup_chiller / 3
        elect_dem += backup_chiller_elect

        # electricity balance
        elect_bal = elect_dem - total_elect_gen
        import_elect = max(0, elect_bal)
        export_elect = abs(min(0, elect_bal))

        # all prices in £/MWh
        gas_price = true_state['Gas price']
        import_price = true_state['Import electricity price']
        export_price = true_state['Export electricity price']
        gas_cost = (gas_price * gas_burned) / 2  # £/HH
        import_cost = (import_price * import_elect) / 2  # £/HH
        export_revenue = (export_price * export_elect) / 2  # £/HH

        reward = export_revenue - (gas_cost + import_cost)  # £/HH

        SP = true_state['Settlement period']
        total_heat_demand = HGH_dem + LGH_dem
        self.info.append([SP,
                          total_elect_gen,
                          import_price,
                          total_heat_demand])

        self.steps += int(1)
        if self.steps == (len(self.ts) - self.lag - 1):  # TODO
            self.done = True

        next_visible_state = self.ts.iloc[self.steps, 1:].values
        next_state = next_visible_state

        next_state = self.state
        self.last_actions = [var['Current']
                             for asset in self.asset_models
                             for var in asset.variables]

        self.action_space, self.lows, self.highs = self.create_action_space(self.last_actions)

        return next_state, reward, self.done, self.info

    """""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
                                Non-Open AI methods
    """""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

    def load_data(self, episode_length):
        ts = pd.read_csv('assets/time_series.csv', index_col=[0])
        ts = ts.iloc[:episode_length, :]
        ts.iloc[:, 1:] = ts.iloc[:, 1:].apply(pd.to_numeric)
        ts.loc[:, 'Timestamp'] = ts.loc[:, 'Timestamp'].apply(pd.to_datetime)
        return ts

    def create_obs_space(self):
        states, self.state_names = [], []
        for mdl in self.state_models:
            states.append([mdl['Min'], mdl['Max']])
            self.state_names.append(mdl['Name'])
        return spaces.MultiDiscrete(states)

    def state_mins_maxs(self):
        s_mins, s_maxs = np.array([]), np.array([])
        for mdl in self.state_models:
            s_mins = np.append(s_mins, mdl['Min'])
            s_maxs = np.append(s_maxs, mdl['Max'])
        return s_mins, s_maxs

    def create_action_space(self, last_actions):
        # available actions are not constant - depend on asset current var
        # spaces = used to define legitimate action space
        actions, lows, highs = [], [], []
        for j, asset in enumerate(self.asset_models):
            current = last_actions[j]
            radius = asset.variables[0]['Radius']
            current_min = asset.variables[0]['Min']
            current_max = asset.variables[0]['Max']
            lower_bound = max(current - radius, current_min)
            upper_bound = min(current + radius, current_max)

            off = gym.spaces.Box(low=0, high=0, shape=(1))

            minimum = gym.spaces.Box(low=current_min,
                                     high=current_min,
                                     shape=(1))

            current_space = gym.spaces.Box(low=lower_bound,
                                           high=upper_bound,
                                           shape=(1))

            if current == 0:  # off
                action = gym.spaces.Tuple((off, minimum))
                low = min(off.low, minimum.low)
                high = max(off.high, minimum.high)
            elif current == current_min:  # at minimum load
                action = gym.spaces.Tuple((off, current_space))
                low = min(off.low, current_space.low)
                high = max(off.high, current_space.high)
            else:
                action = current_space
                low = current_space.low
                high = current_space.high
            actions.append(action)
            lows.append(low)
            highs.append(high)
        return actions, lows, highs

    def asset_mins_maxs(self):
        a_mins, a_maxs = [], []
        for j, asset in enumerate(self.asset_models):
            for var in asset.variables:
                a_mins = np.append(a_mins, var['Min'])
                a_maxs = np.append(a_maxs, var['Max'])
        return a_mins, a_maxs

    def asset_states(self):
        for asset in self.asset_models:
            for var in asset.variables:
                print(var['Name'] + ' is ' + str(var['Current']))
        return self
