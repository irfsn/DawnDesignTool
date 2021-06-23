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

        return sol.value(wing_span)
    except Exception as e:
        print(e)

        return np.NaN


# latitudes = np.linspace(-80, 80, 15)
# day_of_years = np.linspace(0, 365, 30)
# spans = []
# days = []
# lats = []
# num = 0
# for lat_val in latitudes:
#     for day_val in day_of_years:
#         print("\n".join([
#             "-" * 50,
#             f"latitude: {lat_val}",
#             f"day of year: {day_val}",
#         ]))
#         opti.set_value(latitude, lat_val)
#         opti.set_value(day_of_year, day_val)
#
#         try:
#             with time_limit(60 if num < 5 else 20):
#                 sol = opti.solve()
#             opti.set_initial(opti.value_variables())
#             opti.set_initial(opti.lam_g, sol.value(opti.lam_g))
#
#             lats.append(lat_val)
#             days.append(day_val)
#             spans.append(sol.value(wing_span))
#
#         except Exception as e:
#             print(e)
#
#         num += 1


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

    with mp.Pool(mp.cpu_count()) as p:
        spans = p.starmap(
            run,
            inputs,
        )

    np.save("cache/lats" + cache_suffix, lats)
    np.save("cache/days" + cache_suffix, days)
    np.save("cache/spans" + cache_suffix, spans)