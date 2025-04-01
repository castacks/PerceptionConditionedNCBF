"""Test script for DrivingContinuousRandom environment."""

import posggym
import numpy as np
import time
from posggym.envs.continuous.driving_continuous import ExponentialSensorModel

# Create a sensor model with exponential decay
sensor_model = ExponentialSensorModel(beta=1.0)  # Higher beta means faster decay

# Create the environment
env = posggym.make(
    "DrivingContinuousRandom-v0",
    render_mode="human",
    obstacle_density=0.3,  # Increase density for more obstacles
    obstacle_radius_range=(0.5, 1.0),  # Larger obstacles
    random_seed=42,  # Set seed for reproducibility
    sensor_model=sensor_model,  # Use our exponential sensor model
    n_sensors=32,
    obs_dist=5,
    num_agents=1,
)

print(f"Environment created: {env}")
print(f"Agents: {env.agents}")
print(f"Action spaces: {env.action_spaces}")
print(f"Observation spaces: {env.observation_spaces}")

# Reset the environment
obs, info = env.reset()
print(f"Initial observation: {obs.keys()}")

# Run a few random steps
for i in range(200):
    # Sample random actions for all agents
    actions = {agent: env.action_spaces[agent].sample() for agent in env.agents}
    
    # Step the environment
    step_result = env.step(actions)
    
    # Unpack the step result based on its structure
    if isinstance(step_result, tuple) and len(step_result) == 6:
        # Format: (obs, rewards, terminations, truncations, done, infos)
        next_obs, rewards, terminations, truncations, _, infos = step_result
    else:
        # Standard format: (obs, rewards, terminations, truncations, infos)
        next_obs, rewards, terminations, truncations, infos = step_result
    
    # Print some information (only every 10 steps to avoid too much output)
    if i % 10 == 0:
        print(f"\nStep {i}:")
        print(f"Rewards: {rewards}")
        print(f"Terminations: {terminations}")
        print(f"Truncations: {truncations}")
        print(f"Info: {infos}")
    
    # Check for collisions and print them
    for agent_id, info in infos.items():
        if 'outcome' in info and hasattr(info['outcome'], 'value') and info['outcome'].value == -1:
            print(f"\nCOLLISION DETECTED at step {i} for agent {agent_id}!")
            print(f"Reward: {rewards[agent_id]}")
    
    # Slow down the rendering
    env.render()
    time.sleep(0.1)
    
    # Check if all agents are done
    if all(terminations.values()) or all(truncations.values()):
        print("\nEpisode finished, resetting environment")
        obs, info = env.reset()

# Close the environment
env.close()