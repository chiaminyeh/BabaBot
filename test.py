import matplotlib.pyplot as plt
import numpy as np
from scipy.stats import binom

p_values = np.arange(0.1, 1.0, 0.1)

# Calculate probabilities for each option using the survival function (1 - cdf)
# binom.sf(k-1, n, p) is equivalent to P(X >= k)
prob_a = binom.sf(0, 6, p_values) # At least 1 win in 6
prob_b = binom.sf(1, 12, p_values) # At least 2 wins in 12
prob_c = binom.sf(2, 18, p_values) # At least 3 wins in 18

plt.plot(p_values, prob_a, label='Option A', marker='o')
plt.plot(p_values, prob_b, label='Option B', marker='s')
plt.plot(p_values, prob_c, label='Option C', marker='^')

plt.xlabel('Probability of winning a single game (p)')
plt.ylabel('Probability of winning prize')
plt.legend()
plt.show()