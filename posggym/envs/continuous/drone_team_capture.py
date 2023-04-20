"""The Drone Team Capture Environment."""
import math
from typing import Dict, List, NamedTuple, Optional, Tuple, cast

import numpy as np
from gymnasium import spaces

import posggym.model as M
from posggym import logger
from posggym.core import DefaultEnv
from posggym.envs.continuous.core import (
    CircularContinuousWorld,
    PMBodyState,
    clip_actions,
)
from posggym.utils import seeding


class DTCState(NamedTuple):
    """A state in the Drone Team Capture Environment."""

    pursuer_states: np.ndarray
    prev_pursuer_states: np.ndarray
    target_state: np.ndarray
    prev_target_state: np.ndarray
    target_vel: float


DTCAction = np.ndarray
DTCObs = np.ndarray


class DroneTeamCaptureEnv(DefaultEnv[DTCState, DTCObs, DTCAction]):
    """The Drone Team Capture Environment.

    A co-operative 2D continuous world problem involving multiple pursuer drone agents
    working together to catch a target agent in the environment.

    This is an adaption of the original code base to allow for partially observable
    environments.

    Possible Agents
    ---------------
    Varied number (1-8)

    State Space
    -----------
    Each state consists of:

    1. 2D array containing the state of each pursuer
    2. 2D array containing the previous state of each pursuer
    3. 1D array containing the state of the target
    4. 1D array containing the previous state of the target
    5. The velocity of the target (between 0.5 and 2.0 time max velocity of pursuers)

    The state of each pursuer and the target are a 1D array containing their:

    - `(x, y)` coordinate
    - angle/yaw (in radians)
    - velocity in x and y directions
    - angular velocity (in radians)


    Action Space
    ------------
    Each agent has either 1 or 2 actions. If 'velocity_control=False' then the agent
    can only control their angular velocity. If 'velocity_control=True' then the agent
    has two actions, which are the angular and linear velocity, in that order.

    Observation Space
    -----------------
    Each agent receives a 1D vector observation containing information about their
    current state as well as some information about the other pursuers and the target.

    - *Self obs* - observe angle that pursuer is facing and current angular velocity.
    - *Target obs* - observe the angle and distance to the target (if target is within
        `observation_limit`), as well as rate of change of angle and distance to the
        target.
    - *Other pursuer obs* - observes the angle and distance to the
        `n_communicating_pursuers` closest pursuer agents (if they are within
        `observation_limit` distance).

    All observation features have values normalized into [-1, 1] interval. Observation
    feature values are `-1` for any pursuers or the target if they are out of range
    observation range.

    This table enumerates the observation space:

    | Index: start          | Description                          |  Values   |
    | :-------------------: | :----------------------------------: | :-------: |
    | 0                     | Agent angle                          | [-1, 1[   |
    | 1                     | Agent angular velocity               | [-1, 1]   |
    | 2                     | Angle to target                      | [-1, 1]   |
    | 3                     | Distance to target                   | [-1, 1]   |
    | 4                     | Angular velocity of angle to target  | [-1, 1]   |
    | 5                     | Velocity of distance to target       | [-1, 1]   |
    | 6 to (6 + 2 * n)      | Other pursuer angle and distance     | [-1, 1]   |

    Where `n = n_communicating_pursuers`.

    Rewards
    -------
    Each pursuer will receive a reward based on the Q parameter and the distance from
    the target. On successful capture, the capturing pursuer will receive a reward of
    `+130`, while other agents will receive `100`.

    Dynamics
    --------
    Actions of the pursuer agents are deterministic and consist of moving based on the
    angular and linear velocity.

    The target has a maximum velocity that varies per episodes and it actively moves
    away from the pursuers and also the walls.

    Starting State
    --------------
    Target will start near the outside of the circle, while pursuers will start in a
    line on the middle.

    The velocity of the target is chosen uniformly at random at the start of each
    episode to be between 0.5 and 2.0 times the max velocity of the pursuers.

    Episodes End
    ------------
    Episodes ends when the target has been captured. By default a `max_episode_steps`
    limit of `100` steps is also set. This may need to be adjusted when using larger
    world sizes (this can be done by manually specifying a value for
    `max_episode_steps` when creating the environment with `posggym.make`).


    Arguments
    ---------

    - `num_agents` - The number of agents which exist in the environment
        Must be between 1 and 8 (default = `3`)
    - `n_communicating_pursuers - The maximum number of agents which an
        agent can receive information from (default = `3`)
    - `velocity_control` - If the agents have control of their linear velocity
        (default = `False`)
    - `arena_size` - Size of the arena (default = `430`)
    - `observation_limit` - The limit of which agents can see other agents
        (default = `430`)
    - `capture_radius` - Distance from target pursuer needs to be within to capture
        target. As per original paper, the user can adjust this to set a learning
        curriculum (larger values are easier) (default = `30`, which is the radius of
        the target).


    Available variants
    ------------------
    For example to use the Drone Team Capture environment with 8 pursuer drones, with
    communication between max 4 closest drones and episode step limit of 100, and the
    default values for the other parameters (`velocity_control`, `arena_size`,
    `observation_limit`, `capture_radius`) you would use:

    ```python
    import posggym
    env = posggym.make(
        'DroneTeamCapture-v0',
        max_episode_steps=100,
        num_agents=8,
        n_communicating_pursuers=4,
    )
    ```

    Version History
    ---------------
    - `v0`: Initial version

    Reference
    ---------
    - C. de Souza, R. Newbury, A. Cosgun, P. Castillo, B. Vidolov and D. Kulić,
      "Decentralized Multi-Agent Pursuit Using Deep Reinforcement Learning,"
      in IEEE Robotics and Automation Letters, vol. 6, no. 3, pp. 4552-4559,
      July 2021, doi: 10.1109/LRA.2021.3068952.

    """

    metadata = {
        "render_modes": ["human", "rgb_array"],
        "render_fps": 15,
    }

    def __init__(
        self,
        num_agents: int = 3,
        n_communicating_pursuers: int = 3,
        arena_size: float = 430,
        observation_limit: float = 430,
        velocity_control: bool = False,
        capture_radius: float = 30,
        render_mode: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(
            DroneTeamCaptureModel(
                num_agents,
                n_communicating_pursuers,
                velocity_control,
                arena_size=arena_size,
                observation_limit=observation_limit,
                capture_radius=capture_radius,
                **kwargs,
            ),
            render_mode=render_mode,
        )

        self._viewer = None
        self.window_surface = None
        self._renderer = None
        self.render_mode = render_mode
        self.clock = None

        self.window_size = 600
        self.draw_options = None
        self.world = None

    def render(self):
        if self.render_mode is None:
            assert self.spec is not None
            logger.warn(
                "You are calling render method without specifying any render mode. "
                "You can specify the render_mode at initialization, "
                f'e.g. posggym.make("{self.spec.id}", render_mode="rgb_array")'
            )
            return
        if self.render_mode in ("human", "rgb_array"):
            return self._render_img()
        else:
            logger.warn(
                "You are calling render method on an invalid render mode"
                'Continuous environments currently only support "human" or'
                '"rgb_array" render modes.'
                "You can specify the render_mode at initialization, "
                f'e.g. posggym.make("{self.spec.id}", render_mode="rgb_array")'
            )
            return

    def _render_img(self):
        import pygame
        from pymunk import Transform, pygame_util

        model = cast(DroneTeamCaptureModel, self.model)
        state = cast(DTCState, self.state)
        scale_factor = self.window_size / model.world.size

        if self.window_surface is None:
            pygame.init()
            if self.render_mode == "human":
                pygame.display.init()
                pygame.display.set_caption(self.__class__.__name__)
                self.window_surface = pygame.display.set_mode(
                    (self.window_size, self.window_size)
                )
            else:
                self.window_surface = pygame.Surface(
                    (self.window_size, self.window_size)
                )
            # Turn off alpha since we don't use it.
            self.window_surface.set_alpha(None)

        if self.clock is None:
            self.clock = pygame.time.Clock()

        if self.draw_options is None:
            pygame_util.positive_y_is_up = False
            self.draw_options = pygame_util.DrawOptions(self.window_surface)
            self.draw_options.transform = Transform.scaling(scale_factor)
            # don't render collision lines
            self.draw_options.flags = (
                pygame_util.DrawOptions.DRAW_SHAPES
                | pygame_util.DrawOptions.DRAW_CONSTRAINTS
            )

        if self.world is None:
            # get copy of model world, so we can use it for rendering without
            # affecting the original
            self.world = model.world.copy()

        for i, p_state in enumerate(state.pursuer_states):
            self.world.set_entity_state(f"pursuer_{i}", p_state)

        self.world.set_entity_state("evader", state.target_state)

        # Need to do this for space to update with changes
        self.world.space.step(0.0001)

        # reset screen
        self.window_surface.fill(pygame.Color("white"))

        self.world.space.debug_draw(self.draw_options)

        if self.render_mode == "human":
            pygame.event.pump()
            pygame.display.update()
            self.clock.tick(self.metadata["render_fps"])
            return None

        return np.transpose(
            np.array(pygame.surfarray.pixels3d(self.window_surface)), axes=(1, 0, 2)
        )


class DroneTeamCaptureModel(M.POSGModel[DTCState, DTCObs, DTCAction]):
    """The Drone Team Capture problem model.

    Parameters
    ----------
    num_agents: int
        The number of agents which exist in the environment. Must be between 1 and 8
    n_communicating_pursuers: int
        The maximum number of agents which an agent can receive information from
    velocity_control: bool
        If the agents have control of their linear velocity
    arena_size : float
        Size of the arena, in terms of its radius
    observation_limit : float
        The limit of which agents can see other agents
    capture_radius : float
        Distance from target pursuer needs to be within to capture target. As per
        original paper, the user can adjust this to set a learning curriculum (larger
        values are easier).

    """

    R_MAX = 130
    PURSUER_COLOR = (55, 155, 205, 255)  # blueish
    EVADER_COLOR = (110, 55, 155, 255)  # purpleish

    def __init__(
        self,
        num_agents: int,
        n_communicating_pursuers: int,
        velocity_control: bool = False,
        arena_size: float = 430,
        observation_limit: float = 430,
        capture_radius: float = 30,
    ):
        assert 1 < num_agents <= 8
        self.n_pursuers = num_agents
        self.n_com_pursuers = n_communicating_pursuers
        self.velocity_control = velocity_control
        self.r_arena = arena_size
        self.capture_radius = capture_radius
        self.observation_limit = observation_limit

        # Fixed model params
        # Linear Velocity of pursuer
        self.max_pursuer_vel = 10
        # Min and Max relative linear velocity of target
        self.target_rel_vel_bounds = (0.5, 2.0)
        # max relative change in position between any two entities
        self.max_rel_dist_change = self.max_pursuer_vel * (
            1 + self.target_rel_vel_bounds[1]
        )
        self.norm_max_rel_dist_change = self.max_rel_dist_change / (2 * self.r_arena)

        self.possible_agents = tuple(str(x) for x in range(self.n_pursuers))
        self.is_symmetric = True

        self.dyaw_limit = math.pi / 10
        if self.velocity_control:
            # act[0] = angular velocity, act[1] = linear velocity
            self.action_spaces = {
                i: spaces.Box(
                    low=np.array([-self.dyaw_limit, 0], dtype=np.float32),
                    high=np.array([self.dyaw_limit, 1], dtype=np.float32),
                )
                for i in self.possible_agents
            }
        else:
            # act[0] = angular velocity
            self.action_spaces = {
                i: spaces.Box(
                    low=np.array([-self.dyaw_limit], dtype=np.float32),
                    high=np.array([self.dyaw_limit], dtype=np.float32),
                )
                for i in self.possible_agents
            }

        # Agent can see more detailed info about target + self (6 features)
        # Agent can see distance + angle to other pursuers (2 features per pursuer)
        obs_dim = 6 + self.n_com_pursuers * 2
        self.observation_spaces = {
            i: spaces.Box(low=-1.0, high=1.0, shape=(obs_dim,), dtype=np.float32)
            for i in self.possible_agents
        }

        self.world = CircularContinuousWorld(
            size=self.r_arena * 2,
            agent_radius=30.0,
            blocks=None,
            enable_agent_collisions=False,
            border_thickness=2,
        )

        # Add physical entities to the world
        for i in range(self.n_pursuers):
            self.world.add_entity(f"pursuer_{i}", None, color=self.PURSUER_COLOR)
        self.world.add_entity("evader", None, color=self.EVADER_COLOR)

    def get_agents(self, state: DTCState) -> List[M.AgentID]:
        return list(self.possible_agents)

    @property
    def reward_ranges(self) -> Dict[M.AgentID, Tuple[float, float]]:
        return {i: (0.0, self.R_MAX) for i in self.possible_agents}

    @property
    def rng(self) -> seeding.RNG:
        if self._rng is None:
            self._rng, seed = seeding.std_random()
        return self._rng

    def sample_initial_state(self) -> DTCState:
        pursuer_states = np.zeros(
            (self.n_pursuers, PMBodyState.num_features()), dtype=np.float32
        )
        for i in range(self.n_pursuers):
            # distributes the agents based on their index
            x = 50.0 * (-math.floor(self.n_pursuers / 2) + i) + self.r_arena
            pursuer_states[i][:3] = (x, self.r_arena, 0.0)

        bounds = self.r_arena * 0.7
        x = self.rng.random() * (bounds * 2) - bounds + self.r_arena
        y = self.rng.random() * (bounds * 2) - bounds + self.r_arena

        # The velocity of the target varies
        min_rvel, max_rvel = self.target_rel_vel_bounds
        relative_target_vel = float(
            self.rng.random() * (max_rvel - min_rvel) + min_rvel
        )
        target_vel = relative_target_vel * self.max_pursuer_vel

        target_state = np.zeros((PMBodyState.num_features()), dtype=np.float32)
        target_state[:3] = (x, y, 0.0)

        return DTCState(
            pursuer_states,
            np.copy(pursuer_states),
            target_state,
            np.copy(target_state),
            target_vel,
        )

    def sample_initial_obs(self, state: DTCState) -> Dict[M.AgentID, DTCObs]:
        return self._get_obs(state)

    def step(
        self, state: DTCState, actions: Dict[M.AgentID, DTCAction]
    ) -> M.JointTimestep[DTCState, DTCObs]:
        clipped_actions = clip_actions(actions, self.action_spaces)
        next_state = self._get_next_state(state, clipped_actions)
        obs = self._get_obs(state)
        all_done, rewards = self._get_rewards(state)
        terminations = {i: all_done for i in self.possible_agents}
        truncations = {i: False for i in self.possible_agents}
        infos: Dict[M.AgentID, Dict] = {i: {} for i in self.possible_agents}
        return M.JointTimestep(
            next_state, obs, rewards, terminations, truncations, all_done, infos
        )

    def _get_next_state(
        self, state: DTCState, actions: Dict[M.AgentID, DTCAction]
    ) -> DTCState:
        for i in range(self.n_pursuers):
            self.world.set_entity_state(f"pursuer_{i}", state.pursuer_states[i])
            velocity_factor = 1 if not self.velocity_control else actions[str(i)][1]
            pursuer_angle = state.pursuer_states[i][2] + actions[str(i)][0]
            pursuer_vel = self.world.linear_to_xy_velocity(
                velocity_factor * self.max_pursuer_vel, pursuer_angle
            )
            self.world.update_entity_state(
                f"pursuer_{i}",
                angle=pursuer_angle,
                vel=pursuer_vel,
            )

        evader_vel_xy = self._get_target_move_repulsive(state)
        self.world.set_entity_state("evader", state.target_state)
        self.world.update_entity_state("evader", vel=evader_vel_xy)

        self.world.simulate(1.0 / 10, 10)

        next_pursuer_states = np.array(
            [
                self.world.get_entity_state(f"pursuer_{i}")
                for i in range(self.n_pursuers)
            ],
            dtype=np.float32,
        )
        return DTCState(
            next_pursuer_states,
            np.copy(state.pursuer_states),
            np.array(self.world.get_entity_state("evader"), dtype=np.float32),
            np.copy(state.target_state),
            state.target_vel,
        )

    def _get_obs(self, state: DTCState) -> Dict[M.AgentID, DTCObs]:
        observation = {}
        for i in range(self.n_pursuers):
            # getting the target engagement
            (alpha_t, dist_t), target_visible = self._engagement(
                state.pursuer_states[i],
                state.target_state,
                dist_norm_factor=2 * self.r_arena,
            )
            (alpha_t_prev, dist_t_prev), target_prev_visible = self._engagement(
                state.prev_pursuer_states[i],
                state.prev_target_state,
                dist_norm_factor=2 * self.r_arena,
            )
            # change in alpha
            if not target_visible or not target_prev_visible:
                alpha_rate = -1.0
                dist_rate = -1.0
            else:
                # alpha_t and alpha_t_prev are both normalized into [-1, 1] range
                # so have to do some shenanigans to ensure alpha rate is correctly
                # normalized into [-1, 1]
                alpha_rate = (
                    self.world.convert_angle_to_negpi_pi_interval(
                        (alpha_t - alpha_t_prev) * math.pi
                    )
                    / math.pi
                )
                max_rate = self.norm_max_rel_dist_change
                dist_rate = self.world.convert_into_interval(
                    dist_t - dist_t_prev, -max_rate, max_rate, -1.0, 1.0
                )

            # getting the relative engagement
            # alpha and distance to each pursuer
            engagement = []
            for j in range(self.n_pursuers):
                if j == i:
                    eng = (-1.0, -1.0)
                else:
                    eng, _ = self._engagement(
                        state.pursuer_states[i],
                        state.pursuer_states[j],
                        dist_norm_factor=2 * self.r_arena,
                    )
                engagement.append(eng)

            # Put any invalid (-1) to the end
            engagement = sorted(
                engagement, key=lambda t: float("inf") if t[1] == -1.0 else t[1]
            )
            alphas, dists = list(zip(*engagement))

            angle_i = (
                self.world.convert_angle_to_negpi_pi_interval(
                    state.pursuer_states[i][2]
                )
                / math.pi
            )
            prev_angle_i = (
                self.world.convert_angle_to_negpi_pi_interval(
                    state.prev_pursuer_states[i][2]
                )
                / math.pi
            )
            turn_rate = angle_i - prev_angle_i

            # Create obs vector
            obs_i = [
                angle_i,
                turn_rate,
                alpha_t,
                dist_t,
                alpha_rate,
                dist_rate,
            ]

            for idx in range(self.n_com_pursuers):
                obs_i.append(alphas[idx])
                obs_i.append(dists[idx])

            observation[str(i)] = np.array(obs_i, dtype=np.float32)

        return observation

    def _get_rewards(self, state: DTCState) -> Tuple[bool, Dict[M.AgentID, float]]:
        done = False
        reward: Dict[M.AgentID, float] = {}
        q_formation = self._q_parameter(state)
        for i in self.possible_agents:
            reward[i] = -q_formation * 0.1
            target_dist = self._target_distance(state, int(i))
            reward[i] -= 0.002 * target_dist
            if target_dist < self.capture_radius:
                done = True
                reward[i] += 30.0  # 30% more than the others

        if done:
            # Large possible reward when done!
            for i in self.possible_agents:
                reward[i] += 100.0

        return done, reward

    def _engagement(
        self, agent_i: np.ndarray, agent_j: np.ndarray, dist_norm_factor: float
    ) -> Tuple[Tuple[float, float], bool]:
        """Get engagement between two agents.

        Engagement here is the angle (in radians) from agent_i's current position and
        angle to agent_j's position, as well as the distance between the two agents
        positions. Both angle and distance are normalized to [-1, 1] range.

        Note, if agents are outside of observation distance of each other then returns
        (-1, -1), and False.


        """
        dist = self.world.euclidean_dist(agent_i, agent_j)
        if dist > self.observation_limit:
            return (-1.0, -1.0), False

        # Rotation matrix of yaw
        yaw = agent_i[2]
        rot = np.array(
            [[math.cos(yaw), math.sin(yaw)], [-math.sin(yaw), math.cos(yaw)]]
        )
        rel_xy = np.array([agent_i[0] - agent_j[0], agent_i[1] - agent_j[1]])
        rel_xy = rot.dot(rel_xy)
        alpha = math.atan2(rel_xy[1], rel_xy[0])
        alpha = self.world.convert_angle_to_negpi_pi_interval(alpha)
        return (alpha / math.pi, dist / dist_norm_factor), True

    def _q_parameter(self, state: DTCState) -> float:
        """Calculate Q-formation value."""
        closest = self._get_closest_pursuer(state)
        unit = self._get_unit_vectors(state)
        Qk = 0.0
        for i in range(self.n_pursuers):
            if i != closest:
                Qk += np.dot(unit[i], unit[closest]) + 1.0
        Qk /= self.n_pursuers
        return Qk

    def _get_closest_pursuer(self, state: DTCState) -> int:
        """Get closest pursuer to target."""
        min_dist, min_index = None, None
        for idx in range(len(state.pursuer_states)):
            dist = self._target_distance(state, idx)
            if min_dist is None or dist < min_dist:
                min_index = idx
                min_dist = dist
        if min_dist is None or min_index is None:
            raise Exception("No closest index found. Something has gone wrong.")
        return min_index

    def _get_unit_vectors(self, state: DTCState) -> List[List[float]]:
        """Get unit vectors between target and each pursuer."""
        unit = []
        for p in state.pursuer_states:
            dist = self.world.euclidean_dist(p, state.target_state)
            unit.append([p[0] / dist, p[1] / dist])
        return unit

    def _get_target_move_repulsive(self, state: DTCState) -> Tuple[float, float]:
        xy_pos = state.target_state[:2]
        x, y = xy_pos

        def scale_fn(z):
            return 50000 / (abs(z) + 200) ** 2

        final_vector = [0.0, 0.0]
        for i, s in enumerate(state.pursuer_states):
            vector = np.array(s[:2]) - xy_pos
            final_vector = self._scale_vector(vector, scale_fn, final_vector)

        # Find closest point on border then put it in to the vectorial sum
        # Noting coords are with origin at top left, so must translate to where origin
        # is center of circle to get closest point on border, then translate back before
        # adding to vectorial sum
        gamma = -math.atan2(y - self.r_arena, x - self.r_arena)
        virtual_wall = [
            self.r_arena + self.r_arena * math.cos(gamma),
            self.r_arena + -self.r_arena * math.sin(gamma),
        ]
        vector = virtual_wall - xy_pos
        final_vector = self._scale_vector(
            vector, scale_fn, final_vector, 0.5 * len(state.pursuer_states)
        )

        scaled_move_dir = [v / self._abs_sum(final_vector) for v in final_vector]
        dx, dy = scaled_move_dir[0], scaled_move_dir[1]
        d = np.linalg.norm([dx, dy])
        dx = float(state.target_vel * dx / d)
        dy = float(state.target_vel * dy / d)
        return dx, dy

    def _scale_vector(self, vector, scale_fn, final_vector, factor=1.0) -> List[float]:
        vec_sum = self._abs_sum(vector)
        div = max(0.00001, vec_sum)
        f = -factor * scale_fn(vec_sum) / div
        vector = f * vector
        return [x + y for x, y in zip(final_vector, vector)]

    def _abs_sum(self, vector: List[float]) -> float:
        return sum([abs(x) for x in vector])

    def _target_distance(self, state: DTCState, pursuer_idx: int) -> float:
        return self.world.euclidean_dist(
            state.pursuer_states[pursuer_idx], state.target_state
        )
