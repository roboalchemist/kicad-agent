# PCB Autorouter Guidance

## Algorithm Selection
- **Simulated annealing** — best general-purpose placement optimizer
- **Force-directed** — good for initial coarse placement, then refine with SA
- **PathFinder** — iterative congestion-aware routing (rip-up and re-route)
- **Push-and-shove** — interactive routing for design closure

## Cost Function Priority
1. Timing constraints (if high-speed)
2. Routability / congestion
3. Total wirelength (HPWL)
4. Thermal distribution
5. Manufacturing yield (DRC violations)

## Best Practice
- Human-guided placement of critical components + algorithmic routing of the rest
- Fix power, clock, and high-speed component positions manually
- Let autorouter handle standard signal connections
- Always run DRC after autorouting — zero violations required
