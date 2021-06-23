from design_opt import *
from aerosandbox.visualization.carpet_plot_utils import time_limit, patch_nans
import aerosandbox.numpy as np
import multiprocessing as mp

cache_suffix = "_10kg_payload"


def run(lat_val, day_val):
    print("\n".join([
        "-" * 50,
        f"latitude: {lat_val}",
        f"day of year: {day_val}",
    ]))

    opti.set_value(latitude, lat_val)
    opti.set_value(day_of_year, day_val)

    try:
        with time_limit(60):
            sol = opti.solve(verbose=False)
            print("Success!")

        return sol.value(wing_span)
    except Exception as e:
        print("Fail!")
        print(e)

        return np.NaN


if __name__ == '__main__':

    ### Define sweep range
    latitudes = np.linspace(-80, 80, 15)
    day_of_years = np.linspace(0, 365, 30)

    ### Make inputs into 1D lists of inputs
    Latitudes, Day_of_years = np.meshgrid(latitudes, day_of_years)
    lats = Latitudes.flatten()
    days = Day_of_years.flatten()
    inputs = [
        (lat, day)
        for lat, day in zip(lats, days)
    ]

    ### Crunch the numbers, in parallel
    with mp.Pool(mp.cpu_count()) as p:
        spans = p.starmap(
            run,
            inputs,
        )

    ### Save the data
    np.save("cache/lats" + cache_suffix, lats)
    np.save("cache/days" + cache_suffix, days)
    np.save("cache/spans" + cache_suffix, spans)
