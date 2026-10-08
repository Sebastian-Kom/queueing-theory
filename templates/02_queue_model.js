/* Pure browser model. Python supplies independent standard-normal draws.
 * Changing parameters reuses those draws; no random seed is selected for effect.
 */
const CapacityQueue = (() => {
  function validate(mean, std) {
    if (![mean, std].every(x => Number.isFinite(x) && x >= 0 && x <= 1000)) {
      throw new Error('Means and standard deviations must be numbers from 0 to 1000.');
    }
  }
  function counts(shocks, mean, std) {
    validate(mean, std);
    return shocks.map(z => Math.max(0, Math.floor(mean + std * z + 0.5)));
  }
  // erfc approximation (Numerical Recipes); absolute error < 1.3e-7.
  // Count moments shown to three decimals; no probability is used to draw samples.
  function erfc(x) {
    const z = Math.abs(x), t = 1 / (1 + z / 2);
    const v = t * Math.exp(-z*z - 1.26551223 + t*(1.00002368 + t*(0.37409196
      + t*(0.09678418 + t*(-0.18628806 + t*(0.27886807 + t*(-1.13520398
      + t*(1.48851587 + t*(-0.82215223 + t*0.17087277)))))))));
    return x >= 0 ? v : 2 - v;
  }
  function law(mean, std) {
    validate(mean, std);
    if (!std) return {mean:Math.floor(mean + .5), std:0,
      counts:[Math.floor(mean + .5)], probabilities:[1], negative_probability:0};
    const maximum = Math.max(1, Math.ceil(mean + 9*std + .5));
    const survival = k => .5 * erfc((k - .5 - mean) / (std * Math.SQRT2));
    const values = [], probabilities = [];
    let actualMean = 0, probability = 1 - survival(1);
    for (let k = 0; k <= maximum; k++) {
      if (k) probability = Math.max(0, survival(k) - survival(k+1));
      values.push(k); probabilities.push(probability);
      actualMean += k * probability;
    }
    const variance = values.reduce((v, k, i) => v + (k-actualMean)**2*probabilities[i], 0);
    return {mean:actualMean, std:Math.sqrt(Math.max(0,variance)), counts:values,
      probabilities, negative_probability:.5*erfc(mean / (std*Math.SQRT2))};
  }
  function simulate(arrivals, capacities, cents) {
    if (arrivals.length !== capacities.length || ![...arrivals, ...capacities].every(x => Number.isSafeInteger(x) && x >= 0)) {
      throw new Error('Daily counts must be matching nonnegative integer arrays.');
    }
    if (!Number.isSafeInteger(cents) || cents < 0
        || arrivals.reduce((a,b) => a+b,0) * cents > Number.MAX_SAFE_INTEGER) {
      throw new Error('Total development cost exceeds exact integer accounting.');
    }
    const backlog = [0], completed = [], unused = [], live_days = [], cohorts = [];
    const mean_completed_wait = [null];
    let head = 0, totalArrivals = 0, totalTested = 0, totalWait = 0;
    for (let index = 0; index < arrivals.length; index++) {
      const day = index+1, incoming = arrivals[index], capacity = capacities[index];
      const oldest = () => head < cohorts.length ? day-cohorts[head][0] : null;
      const oldestBefore = oldest();
      if (incoming) cohorts.push([day, incoming]);
      const oldestJoined = oldest();
      const tested = Math.min(backlog[index] + incoming, capacity);
      let remaining = tested, waitToday = 0;
      while (remaining) {
        const take = Math.min(remaining, cohorts[head][1]);
        waitToday += take*(day-cohorts[head][0]);
        remaining -= take;
        cohorts[head][1] -= take;
        if (!cohorts[head][1]) head++;
      }
      const after = backlog[index]+incoming-tested;
      const queues = [backlog[index], backlog[index]+incoming, after];
      live_days.push({arrival_first:totalArrivals+1, arrivals:incoming, capacity,
        tested_first:totalTested+1, tested, unused:capacity-tested,
        queue_first:[totalTested+1,totalTested+1,totalTested+tested+1],
        queue_counts:queues, queue_cost_cents:queues.map(q => q*cents),
        oldest_wait:[oldestBefore,oldestJoined,oldest()],
        mean_wait_today:tested ? waitToday/tested : null});
      totalArrivals += incoming; totalTested += tested; totalWait += waitToday;
      backlog.push(after); completed.push(tested); unused.push(capacity-tested);
      mean_completed_wait.push(totalTested ? totalWait/totalTested : null);
    }
    return {arrivals, capacities, backlog, completed, unused, live_days,
      mean_completed_wait, bound_cost_cents:backlog.map(q => q*cents)};
  }
  return {counts, law, simulate, validate};
})();
if (typeof module !== 'undefined' && module.exports) module.exports = CapacityQueue;
