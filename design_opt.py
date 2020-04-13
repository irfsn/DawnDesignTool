# TODO wing loading checks
# TODO implement dongjoon prop model

# Grab AeroSandbox
import aerosandbox as asb
import aerosandbox.library.aerodynamics as aero
import aerosandbox.library.atmosphere as atmo
from aerosandbox.casadi_helpers import *
from aerosandbox.library import mass_structural as lib_mass_struct
from aerosandbox.library import power_gas as lib_gas
from aerosandbox.library import power_solar as lib_solar
from aerosandbox.library import propulsion_electric as lib_prop_elec
from aerosandbox.library import propulsion_propeller as lib_prop_prop
from aerosandbox.library import winds as lib_winds
from aerosandbox.library.airfoils import *
import copy

# region Setup
##### Initialize Optimization
opti = cas.Opti()

##### Operating Parameters
latitude = 49  # degrees (49 deg is top of CONUS, 26 deg is bottom of CONUS)
day_of_year = 244  # Julian day. June 1 is 153, June 22 is 174, Aug. 31 is 244
min_altitude = 19812  # meters. 19812 m = 65000 ft.
required_headway_per_day = 10e3  # meters
days_to_simulate = opti.parameter()
opti.set_value(days_to_simulate, 1)
propulsion_type = "solar"  # "solar" or "gas"
enforce_periodicity = True  # Tip: turn this off when looking at gas models or models w/o trajectory opt. enabled.
allow_trajectory_optimization = True
n_booms = 3  # 1, 2, or 3
structural_load_factor = 3  # over static
structural_mass_margin_multiplier = opti.parameter()  # Mass margin (implemented as a multiplier on total mass)
opti.set_value(structural_mass_margin_multiplier, 1.50)
minimize = "span"  # "span" or "TOGW"
mass_payload = opti.parameter()
opti.set_value(mass_payload, 30)
wind_speed_func = lambda alt: lib_winds.wind_speed_conus_summer_99(alt, latitude)
battery_specific_energy_Wh_kg = opti.parameter()
opti.set_value(battery_specific_energy_Wh_kg, 450)

##### Simulation Parameters
n_timesteps = 150  # Only relevant if allow_trajectory_optimization is True.
# Quick convergence testing indicates you can get bad analyses below 150 or so...

##### Optimization bounds
min_speed = 1  # Specify a minimum speed - keeps the speed-gamma velocity parameterization from NaNing

##### Climb Optimization
climb_opt = False  # are we optimizing for the climb as well
seconds_per_day = 86400
if climb_opt:
    simulation_days = 1.5  # must be greater than 1
    opti.set_value(days_to_simulate, simulation_days)
    enforce_periodicity = False  # Leave False
    time_shift = -6.5 * 60 * 60  # 60*((seconds_per_day*2)/(n_timesteps))
    timesteps_of_last_day = int((1 - (1 / simulation_days)) * n_timesteps)

# endregion

# region Trajectory Optimization Variables
##### Initialize trajectory optimization variables

x = 1e5 * opti.variable(n_timesteps)
opti.set_initial(x, 0)

if not climb_opt:

    y = 2e4 * opti.variable(n_timesteps)
    opti.set_initial(y, 20000, )
    opti.subject_to([
        y / min_altitude > 1,
        y / 40000 < 1,  # models break down
    ])
else:
    y = 2e4 * opti.variable(n_timesteps)
    opti.set_initial(y, 20000)
    opti.subject_to([y[timesteps_of_last_day:-1] > min_altitude, y < 40000])

airspeed = 2e1 * opti.variable(n_timesteps)
opti.set_initial(airspeed, 20)
opti.subject_to([
    airspeed / min_speed > 1
])

flight_path_angle = 1e-1 * opti.variable(n_timesteps)
opti.set_initial(flight_path_angle, 0)
opti.subject_to([
    flight_path_angle / 90 < 1,
    flight_path_angle / 90 > -1,
])

alpha = 4 * opti.variable(n_timesteps)
opti.set_initial(alpha, 3)
opti.subject_to([
    alpha > -8,
    alpha < 12
])

thrust_force = 2e2 * opti.variable(n_timesteps)
opti.set_initial(thrust_force, 150)
opti.subject_to([
    thrust_force > 0
])

net_accel_parallel = 1e-2 * opti.variable(n_timesteps)
opti.set_initial(net_accel_parallel, 0)

net_accel_perpendicular = 1e-1 * opti.variable(n_timesteps)
opti.set_initial(net_accel_perpendicular, 0)

##### Set up time
time_nondim = cas.linspace(0, 1, n_timesteps)
seconds_per_day = 86400
time = time_nondim * days_to_simulate * seconds_per_day
hour = time / 3600

# endregion

# region Design Optimization Variables
##### Initialize design optimization variables (all units in base SI or derived units)
if propulsion_type == "solar":
    # log_mass_total = opti.variable()
    # opti.set_initial(log_mass_total, cas.log(600))
    # mass_total = cas.exp(log_mass_total)

    mass_total = 600 * opti.variable()
    opti.set_initial(mass_total, 600)

    max_mass_total = mass_total
elif propulsion_type == "gas":
    log_mass_total = opti.variable(n_timesteps)
    opti.set_initial(log_mass_total, cas.log(800))
    mass_total = cas.exp(log_mass_total)
    log_max_mass_total = opti.variable()
    opti.set_initial(log_max_mass_total, cas.log(800))
    max_mass_total = cas.exp(log_max_mass_total)
    opti.subject_to([
        log_mass_total < log_max_mass_total
    ])
else:
    raise ValueError("Bad value of propulsion_type!")

### Initialize geometric variables
# wing
wing_span = 60 * opti.variable()
opti.set_initial(wing_span, 60)
opti.subject_to([wing_span > 1])

wing_root_chord = 4 * opti.variable()
opti.set_initial(wing_root_chord, 3)
opti.subject_to([wing_root_chord > 0.1])

wing_x_quarter_chord = 0.01 * opti.variable()
opti.set_initial(wing_x_quarter_chord, 0)

# hstab
hstab_span = 15 * opti.variable()
opti.set_initial(hstab_span, 12)
opti.subject_to(hstab_span > 0.1)

hstab_chord = 2 * opti.variable()
opti.set_initial(hstab_chord, 3)
opti.subject_to([hstab_chord > 0.1])

hstab_twist_angle = 2 * opti.variable(n_timesteps)
opti.set_initial(hstab_twist_angle, -7)

# vstab
vstab_span = 8 * opti.variable()
opti.set_initial(vstab_span, 7)
opti.subject_to(vstab_span > 0.1)

vstab_chord = 2 * opti.variable()
opti.set_initial(vstab_chord, 2.5)
opti.subject_to([vstab_chord > 0.1])

# fuselage

boom_length = opti.variable()
opti.set_initial(boom_length, 23)
opti.subject_to([
    boom_length - vstab_chord - hstab_chord > wing_x_quarter_chord + wing_root_chord * 3 / 4
    # you can relax this, but you need to change the fuselage shape first
])

nose_length = 1.5

fuse_diameter = 0.6
boom_diameter = 0.2

wing = asb.Wing(
    name="Main Wing",
    # x_le=-0.05 * wing_root_chord,  # Coordinates of the wing's leading edge # TODO make this a free parameter?
    x_le=wing_x_quarter_chord,  # Coordinates of the wing's leading edge # TODO make this a free parameter?
    y_le=0,  # Coordinates of the wing's leading edge
    z_le=0,  # Coordinates of the wing's leading edge
    symmetric=True,
    xsecs=[  # The wing's cross ("X") sections
        asb.WingXSec(  # Root
            x_le=-wing_root_chord / 4,  # Coordinates of the XSec's leading edge, relative to the wing's leading edge.
            y_le=0,  # Coordinates of the XSec's leading edge, relative to the wing's leading edge.
            z_le=0,  # Coordinates of the XSec's leading edge, relative to the wing's leading edge.
            chord=wing_root_chord,
            twist=0,  # degrees
            airfoil=e216,  # Airfoils are blended between a given XSec and the next one.
            control_surface_type='symmetric',
            # Flap # Control surfaces are applied between a given XSec and the next one.
            control_surface_deflection=0,  # degrees
        ),
        asb.WingXSec(  # Tip
            x_le=-wing_root_chord * 0.5 / 4,
            y_le=wing_span / 2,
            z_le=0,  # wing_span / 2 * cas.pi / 180 * 5,
            chord=wing_root_chord * 0.5,
            twist=0,
            airfoil=e216,
        ),
    ]
)
hstab = asb.Wing(
    name="Horizontal Stabilizer",
    x_le=boom_length - vstab_chord * 0.75 - hstab_chord,  # Coordinates of the wing's leading edge
    y_le=0,  # Coordinates of the wing's leading edge
    z_le=0.1,  # Coordinates of the wing's leading edge
    symmetric=True,
    xsecs=[  # The wing's cross ("X") sections
        asb.WingXSec(  # Root
            x_le=0,  # Coordinates of the XSec's leading edge, relative to the wing's leading edge.
            y_le=0,  # Coordinates of the XSec's leading edge, relative to the wing's leading edge.
            z_le=0,  # Coordinates of the XSec's leading edge, relative to the wing's leading edge.
            chord=hstab_chord,
            twist=-3,  # degrees # TODO fix
            airfoil=generic_airfoil,  # Airfoils are blended between a given XSec and the next one.
            control_surface_type='symmetric',
            # Flap # Control surfaces are applied between a given XSec and the next one.
            control_surface_deflection=0,  # degrees
        ),
        asb.WingXSec(  # Tip
            x_le=0,
            y_le=hstab_span / 2,
            z_le=0,
            chord=hstab_chord,
            twist=-3,  # TODO fix
            airfoil=generic_airfoil,
        ),
    ]
)
vstab = asb.Wing(
    name="Vertical Stabilizer",
    x_le=boom_length - vstab_chord * 0.75,  # Coordinates of the wing's leading edge
    y_le=0,  # Coordinates of the wing's leading edge
    z_le=-vstab_span / 2 + vstab_span * 0.15,  # Coordinates of the wing's leading edge
    symmetric=False,
    xsecs=[  # The wing's cross ("X") sections
        asb.WingXSec(  # Root
            x_le=0,  # Coordinates of the XSec's leading edge, relative to the wing's leading edge.
            y_le=0,  # Coordinates of the XSec's leading edge, relative to the wing's leading edge.
            z_le=0,  # Coordinates of the XSec's leading edge, relative to the wing's leading edge.
            chord=vstab_chord,
            twist=0,  # degrees
            airfoil=generic_airfoil,  # Airfoils are blended between a given XSec and the next one.
            control_surface_type='symmetric',
            # Flap # Control surfaces are applied between a given XSec and the next one.
            control_surface_deflection=0,  # degrees
        ),
        asb.WingXSec(  # Tip
            x_le=0,
            y_le=0,
            z_le=vstab_span,
            chord=vstab_chord,
            twist=0,
            airfoil=generic_airfoil,
        ),
    ]
)
### Build the fuselage geometry
blend = lambda x: (1 - np.cos(np.pi * x)) / 2
fuse_x_c = []
fuse_z_c = []
fuse_radius = []
fuse_resolution = 10
# Nose geometry
fuse_nose_theta = np.linspace(0, np.pi / 2, fuse_resolution)
fuse_x_c.extend([
    (wing_x_quarter_chord - wing_root_chord / 4) - nose_length * np.cos(theta) for theta in fuse_nose_theta
])
fuse_z_c.extend([-fuse_diameter / 2] * fuse_resolution)
fuse_radius.extend([
    fuse_diameter / 2 * np.sin(theta) for theta in fuse_nose_theta
])
# Taper
fuse_taper_x_nondim = np.linspace(0, 1, fuse_resolution)
fuse_x_c.extend([
    0.0 * boom_length + (0.6 - 0.0) * boom_length * x_nd for x_nd in fuse_taper_x_nondim
])
fuse_z_c.extend([
    -fuse_diameter / 2 * blend(1 - x_nd) - boom_diameter / 2 * blend(x_nd) for x_nd in fuse_taper_x_nondim
])
fuse_radius.extend([
    fuse_diameter / 2 * blend(1 - x_nd) + boom_diameter / 2 * blend(x_nd) for x_nd in fuse_taper_x_nondim
])
# Tail
# fuse_tail_x_nondim = np.linspace(0, 1, fuse_resolution)[1:]
# fuse_x_c.extend([
#     0.9 * boom_length + (1 - 0.9) * boom_length * x_nd for x_nd in fuse_taper_x_nondim
# ])
# fuse_z_c.extend([
#     -boom_diameter / 2 * blend(1 - x_nd) for x_nd in fuse_taper_x_nondim
# ])
# fuse_radius.extend([
#     boom_diameter / 2 * blend(1 - x_nd) for x_nd in fuse_taper_x_nondim
# ])
fuse_straight_resolution = 4
fuse_x_c.extend([
    0.6 * boom_length + (1 - 0.6) * boom_length * x_nd for x_nd in np.linspace(0, 1, fuse_straight_resolution)[1:]
])
fuse_z_c.extend(
    [-boom_diameter / 2] * (fuse_straight_resolution - 1)
)
fuse_radius.extend(
    [boom_diameter / 2] * (fuse_straight_resolution - 1)
)

fuse = asb.Fuselage(
    name="Fuselage",
    x_le=0,
    y_le=0,
    z_le=0,
    xsecs=[
        asb.FuselageXSec(
            x_c=fuse_x_c[i],
            z_c=fuse_z_c[i],
            radius=fuse_radius[i]
        ) for i in range(len(fuse_x_c))
    ]
)

# Assemble the airplane
fuses = []
hstabs = []
vstabs = []
if n_booms == 1:
    fuses.append(fuse)
    hstabs.append(hstab)
    vstabs.append(vstab)
elif n_booms == 2:
    boom_location = 0.40  # as a fraction of the half-span

    left_fuse = copy.deepcopy(fuse)
    right_fuse = copy.deepcopy(fuse)
    left_fuse.xyz_le += cas.vertcat(0, -wing_span / 2 * boom_location, 0)
    right_fuse.xyz_le += cas.vertcat(0, wing_span / 2 * boom_location, 0)
    fuses.extend([left_fuse, right_fuse])

    left_hstab = copy.deepcopy(hstab)
    right_hstab = copy.deepcopy(hstab)
    left_hstab.xyz_le += cas.vertcat(0, -wing_span / 2 * boom_location, 0)
    right_hstab.xyz_le += cas.vertcat(0, wing_span / 2 * boom_location, 0)
    hstabs.extend([left_hstab, right_hstab])

    left_vstab = copy.deepcopy(vstab)
    right_vstab = copy.deepcopy(vstab)
    left_vstab.xyz_le += cas.vertcat(0, -wing_span / 2 * boom_location, 0)
    right_vstab.xyz_le += cas.vertcat(0, wing_span / 2 * boom_location, 0)
    vstabs.extend([left_vstab, right_vstab])

elif n_booms == 3:
    boom_location = 0.57  # as a fraction of the half-span

    left_fuse = copy.deepcopy(fuse)
    center_fuse = copy.deepcopy(fuse)
    right_fuse = copy.deepcopy(fuse)
    left_fuse.xyz_le += cas.vertcat(0, -wing_span / 2 * boom_location, 0)
    right_fuse.xyz_le += cas.vertcat(0, wing_span / 2 * boom_location, 0)
    fuses.extend([left_fuse, center_fuse, right_fuse])

    left_hstab = copy.deepcopy(hstab)
    center_hstab = copy.deepcopy(hstab)
    right_hstab = copy.deepcopy(hstab)
    left_hstab.xyz_le += cas.vertcat(0, -wing_span / 2 * boom_location, 0)
    right_hstab.xyz_le += cas.vertcat(0, wing_span / 2 * boom_location, 0)
    hstabs.extend([left_hstab, center_hstab, right_hstab])

    left_vstab = copy.deepcopy(vstab)
    center_vstab = copy.deepcopy(vstab)
    right_vstab = copy.deepcopy(vstab)
    left_vstab.xyz_le += cas.vertcat(0, -wing_span / 2 * boom_location, 0)
    right_vstab.xyz_le += cas.vertcat(0, wing_span / 2 * boom_location, 0)
    vstabs.extend([left_vstab, center_vstab, right_vstab])

else:
    raise ValueError("Bad value of n_booms!")

airplane = asb.Airplane(
    name="Solar1",
    x_ref=0,
    y_ref=0,
    z_ref=0,
    wings=[wing] + hstabs + vstabs,
    fuselages=fuses,
)

# endregion

# region Atmosphere
##### Atmosphere
P = atmo.get_pressure_at_altitude(y)
rho = atmo.get_density_at_altitude(y)
T = atmo.get_temperature_at_altitude(y)
mu = atmo.get_viscosity_from_temperature(T)
a = atmo.get_speed_of_sound_from_temperature(T)
mach = airspeed / a
g = 9.81  # gravitational acceleration, m/s^2
q = 1 / 2 * rho * airspeed ** 2  # Solar calculations
solar_flux_on_horizontal = lib_solar.solar_flux_on_horizontal(
    latitude, day_of_year, time, scattering=True
)
# endregion

# region Aerodynamics
##### Aerodynamics

# Fuselage
fuse_Re = rho / mu * airspeed * fuse.length()
CLA_fuse = 0
CDA_fuse = aero.Cf_flat_plate(fuse_Re) * fuse.area_wetted() * 1.2  # wetted area with form factor

lift_fuse = CLA_fuse * q  # per fuse
drag_fuse = CDA_fuse * q  # per fuse

# wing
wing_Re = rho / mu * airspeed * wing.mean_geometric_chord()
wing_airfoil = wing.xsecs[0].airfoil  # type: asb.Airfoil
wing_Cl_inc = wing_airfoil.CL_function(alpha + wing.mean_twist_angle(), wing_Re, 0,
                                       0)  # Incompressible 2D lift coefficient
wing_CL = wing_Cl_inc * aero.CL_over_Cl(wing.aspect_ratio(), mach=mach,
                                        sweep=wing.mean_sweep_angle())  # Compressible 3D lift coefficient
lift_wing = wing_CL * q * wing.area()

wing_Cd_profile = wing_airfoil.CDp_function(alpha + wing.mean_twist_angle(), wing_Re, mach, 0)
drag_wing_profile = wing_Cd_profile * q * wing.area()

wing_oswalds_efficiency = 0.95  # TODO make this a function of taper ratio
drag_wing_induced = lift_wing ** 2 / (q * np.pi * wing.span() ** 2 * wing_oswalds_efficiency)

drag_wing = drag_wing_profile + drag_wing_induced

wing_Cm_inc = wing_airfoil.Cm_function(alpha + wing.mean_twist_angle(), wing_Re, 0,
                                       0)  # Incompressible 2D moment coefficient
wing_CM = wing_Cm_inc * aero.CL_over_Cl(wing.aspect_ratio(), mach=mach,
                                        sweep=wing.mean_sweep_angle())  # Compressible 3D moment coefficient
moment_wing = wing_CM * q * wing.area() * wing.mean_geometric_chord()

# hstab
hstab_Re = rho / mu * airspeed * hstab.mean_geometric_chord()
hstab_airfoil = hstab.xsecs[0].airfoil  # type: asb.Airfoil
hstab_Cl_inc = hstab_airfoil.CL_function(alpha + hstab_twist_angle, hstab_Re, 0,
                                         0)  # Incompressible 2D lift coefficient
hstab_CL = hstab_Cl_inc * aero.CL_over_Cl(hstab.aspect_ratio(), mach=mach,
                                          sweep=hstab.mean_sweep_angle())  # Compressible 3D lift coefficient
lift_hstab = hstab_CL * q * hstab.area()  # per hstab

hstab_Cd_profile = hstab_airfoil.CDp_function(alpha + hstab_twist_angle, hstab_Re, mach, 0)
drag_hstab_profile = hstab_Cd_profile * q * hstab.area()

hstab_oswalds_efficiency = 0.95  # TODO make this a function of taper ratio
drag_hstab_induced = lift_hstab ** 2 / (q * np.pi * hstab.span() ** 2 * hstab_oswalds_efficiency)

drag_hstab = drag_hstab_profile + drag_hstab_induced  # per hstab

# vstab
vstab_Re = rho / mu * airspeed * vstab.mean_geometric_chord()
vstab_airfoil = vstab.xsecs[0].airfoil  # type: asb.Airfoil
vstab_Cd_profile = vstab_airfoil.CDp_function(0, vstab_Re, mach, 0)
drag_vstab_profile = vstab_Cd_profile * q * vstab.area()

drag_vstab = drag_vstab_profile  # per vstab

# Force totals
lift_force = lift_wing + n_booms * (lift_fuse + lift_hstab)
drag_force = drag_wing + n_booms * (drag_fuse + drag_hstab + drag_vstab)  # + drag_lift_wires

# Useful metrics
wing_loading = 9.81 * max_mass_total / wing.area()
wing_loading_psf = wing_loading / 47.880258888889

# endregion

# region Stability
### Estimate aerodynamic center
x_ac = (
               wing.approximate_center_of_pressure()[0] * wing.area() +
               hstab.approximate_center_of_pressure()[0] * hstab.area() * n_booms
       ) / (
               wing.area() + hstab.area() * n_booms
       )
static_margin_fraction = (x_ac - airplane.xyz_ref[0]) / wing.mean_geometric_chord()
opti.subject_to([
    static_margin_fraction == 0.2
])

### Trim
net_pitching_moment = (
        -wing.approximate_center_of_pressure()[0] * lift_wing + moment_wing
        - hstab.approximate_center_of_pressure()[0] * lift_hstab * n_booms
)
opti.subject_to([
    net_pitching_moment / 1e4 == 0  # Trim condition
])

### Size the tails off of tail volume coefficients
Vh = hstab.area() * n_booms * (
        hstab.approximate_center_of_pressure()[0] - wing.approximate_center_of_pressure()[0]
) / (wing.area() * wing.mean_geometric_chord())
Vv = vstab.area() * n_booms * (
        vstab.approximate_center_of_pressure()[0] - wing.approximate_center_of_pressure()[0]
) / (wing.area() * wing.span())

hstab_effectiveness_factor = aero.CL_over_Cl(hstab.aspect_ratio()) / aero.CL_over_Cl(wing.aspect_ratio())
vstab_effectiveness_factor = aero.CL_over_Cl(vstab.aspect_ratio()) / aero.CL_over_Cl(wing.aspect_ratio())

opti.subject_to([
    # Vh * hstab_effectiveness_factor > 0.3,
    # Vh * hstab_effectiveness_factor < 0.6,
    # Vh * hstab_effectiveness_factor == 0.45,
    # Vv * vstab_effectiveness_factor > 0.02,
    # Vv * vstab_effectiveness_factor < 0.05,
    # Vv * vstab_effectiveness_factor == 0.035,
    # Vh > 0.3,
    # Vh < 0.6,
    # Vh == 0.45,
    Vv > 0.02,
    # Vv < 0.05,
    # Vv == 0.035,
])

# endregion

# region Propulsion
### Propeller calculations
propeller_diameter = opti.variable()
opti.set_initial(propeller_diameter,
                 5
                 )
opti.subject_to([
    propeller_diameter > 1,
    propeller_diameter < 10
])

n_propellers = 2 * n_booms
# n_propellers = opti.variable()
# opti.set_initial(n_propellers,
#                  2
#                  )
# opti.subject_to([
#     n_propellers > 2,
#     n_propellers < 6,
# ])

area_propulsive = cas.pi / 4 * propeller_diameter ** 2 * n_propellers
propeller_efficiency = 0.8  # a total WAG
motor_efficiency = 0.856 / (0.856 + 0.026 + 0.018 + 0.004)  # back-calculated from Odysseus data

power_out_propulsion_shaft = lib_prop_prop.propeller_shaft_power_from_thrust(
    thrust_force=thrust_force,
    area_propulsive=area_propulsive,
    airspeed=airspeed,
    rho=rho,
    propeller_efficiency=propeller_efficiency
)

power_out_propulsion = power_out_propulsion_shaft / motor_efficiency

power_out_max = 5e3 * opti.variable()
opti.set_initial(power_out_max, 5e3)
opti.subject_to([
    power_out_propulsion < power_out_max,
    power_out_max > 0
])

mass_motor_raw = lib_prop_elec.mass_motor_electric(max_power=power_out_max / n_propellers) * n_propellers
mass_motor_mounted = 2 * mass_motor_raw  # similar to a quote from Raymer, modified to make sensible units, prop weight roughly subtracted

mass_propellers = n_propellers * lib_prop_prop.mass_hpa_propeller(
    diameter=propeller_diameter,
    max_power=power_out_max,
    include_variable_pitch_mechanism=True
)
mass_ESC = lib_prop_elec.mass_ESC(max_power=power_out_max)

# Total propulsion mass
mass_propulsion = mass_motor_mounted + mass_propellers + mass_ESC

# Account for payload power
power_out_payload = cas.if_else(
    solar_flux_on_horizontal > 1,
    500,
    150
)

# Account for avionics power
power_out_avionics = 112  # Pulled from Avionics spreadsheet on 4/10
# https://docs.google.com/spreadsheets/d/1nhz2SAcj4uplEZKqQWHYhApjsZvV9hme9DlaVmPca0w/edit?pli=1#gid=0

### Power accounting
power_out = power_out_propulsion + power_out_payload + power_out_avionics

# endregion


if propulsion_type == "solar":
    # region Solar Power Systems

    # Battery design variables
    net_power = 1000 * opti.variable(n_timesteps)
    opti.set_initial(net_power,
                     0,
                     )

    battery_stored_energy_nondim = 1 * opti.variable(n_timesteps)
    opti.set_initial(battery_stored_energy_nondim,
                     0.5,
                     )
    allowable_battery_depth_of_discharge = 0.9  # How much of the battery can you actually use?
    opti.subject_to([
        battery_stored_energy_nondim > 0,
        battery_stored_energy_nondim < allowable_battery_depth_of_discharge,
    ])

    battery_capacity = 3600 * 40000 * opti.variable()  # Joules, not watt-hours!
    opti.set_initial(battery_capacity,
                     3600 * 20000
                     )
    opti.subject_to([
        battery_capacity > 0
    ])
    battery_capacity_watt_hours = battery_capacity / 3600
    battery_stored_energy = battery_stored_energy_nondim * battery_capacity

    ### Solar calculations

    realizable_solar_cell_efficiency = 0.25
    # This figure should take into account all temperature factors, MPPT losses,
    # spectral losses (different spectrum at altitude), multi-junction effects, etc.
    # Kevin Uleck gives this figure as 0.205.
    # This paper (https://core.ac.uk/download/pdf/159146935.pdf) gives it as 0.19.
    # According to Bjarni, MicroLink Devices has flexible triple-junction cells at 31% and 37.75% efficiency.
    # Bjarni, 4/5/20: "I'd make the approximation that we can get at least 25% (after accounting for MPPT, mismatch; before thermal effects)."

    # Total cell power flux
    solar_power_flux = (
            solar_flux_on_horizontal *
            realizable_solar_cell_efficiency
    )

    solar_area_fraction = opti.variable()
    opti.set_initial(solar_area_fraction,
                     0.5
                     )
    opti.subject_to([
        solar_area_fraction > 0,
        solar_area_fraction < 0.80,  # TODO check
    ])

    area_solar = wing.area() * solar_area_fraction
    power_in = solar_power_flux * area_solar

    # Solar cell weight
    rho_solar_cells = 0.35  # kg/m^2, solar cell area density.
    # The solar_simple_demo model gives this as 0.27. Burton's model gives this as 0.30.
    # This paper (https://core.ac.uk/download/pdf/159146935.pdf) gives it as 0.42.
    # This paper (https://scholarsarchive.byu.edu/cgi/viewcontent.cgi?article=4144&context=facpub) effectively gives it as 0.3143.
    # According to Bjarni, MicroLink Devices has cells on the order of 250 g/m^2 - but they're prohibitively expensive.
    # Bjarni, 4/5/20: "400 g/m^2"
    # 4/10/20: 0.35 kg/m^2 taken from avionics spreadsheet: https://docs.google.com/spreadsheets/d/1nhz2SAcj4uplEZKqQWHYhApjsZvV9hme9DlaVmPca0w/edit?pli=1#gid=0

    mass_solar_cells = rho_solar_cells * area_solar

    ### Battery calculations
    # battery_specific_energy_Wh_kg = opti.parameter()
    # opti.set_value(battery_specific_energy_Wh_kg, 550 if optimistic else 300)
    # Burton's solar model uses 350, and public specs from Amprius seem to indicate that's possible.
    # Jim Anderson believes 550 Wh/kg is possible.
    # Odysseus had cells that were 265 Wh/kg.

    battery_pack_cell_percentage = 0.70  # What percent of the battery pack consists of the module, by weight?
    # Accounts for module HW, BMS, pack installation, etc.
    # Ed Lovelace (in his presentation) gives 70% as a state-of-the-art fraction.

    mass_battery_pack = lib_prop_elec.mass_battery_pack(
        battery_capacity_Wh=battery_capacity_watt_hours,
        battery_cell_specific_energy_Wh_kg=battery_specific_energy_Wh_kg,
        battery_pack_cell_fraction=battery_pack_cell_percentage
    )

    battery_voltage = 240  # From Olek Peraire 4/2, propulsion slack

    # mass_wires = lib_prop_elec.mass_wires(
    #     wire_length=wing.span() / 2,
    #     max_current=power_out_max / battery_voltage,
    #     allowable_voltage_drop=battery_voltage * 0.0225,
    #     material="aluminum"
    # ) # buildup model
    mass_wires = 0.868  # Taken from Avionics spreadsheet on 4/10/20
    # https://docs.google.com/spreadsheets/d/1nhz2SAcj4uplEZKqQWHYhApjsZvV9hme9DlaVmPca0w/edit?pli=1#gid=0

    mass_MPPT = 5.7  # Model taken from Avionics spreadsheet on 4/10/20
    # https://docs.google.com/spreadsheets/d/1nhz2SAcj4uplEZKqQWHYhApjsZvV9hme9DlaVmPca0w/edit?pli=1#gid=0

    mass_power_systems_misc = 0.314  # Taken from Avionics spreadsheet on 4/10/20, includes HV-LV convs. and fault isolation mechs
    # https://docs.google.com/spreadsheets/d/1nhz2SAcj4uplEZKqQWHYhApjsZvV9hme9DlaVmPca0w/edit?pli=1#gid=0

    # Total system mass
    mass_power_systems = mass_solar_cells + mass_battery_pack + mass_wires + mass_MPPT + mass_power_systems_misc

    # endregion
elif propulsion_type == "gas":
    # region Gas Power Systems

    # Gas design variables
    mass_fuel_nondim = 1 * opti.variable(n_timesteps)  # What fraction of the fuel tank is filled at timestep i?
    opti.set_initial(mass_fuel_nondim,
                     cas.linspace(1, 0, n_timesteps)
                     )
    opti.subject_to([
        mass_fuel_nondim > 0,
        mass_fuel_nondim < 1,
    ])
    mass_fuel_max = 100 * opti.variable()  # How many kilograms of fuel in a full tank?
    opti.set_initial(mass_fuel_max,
                     100
                     )
    opti.subject_to([
        mass_fuel_max > 0
    ])
    mass_fuel = mass_fuel_nondim * mass_fuel_max

    # Gas engine
    mass_gas_engine = lib_gas.mass_gas_engine(power_out_max)
    mass_alternator = 0  # for now! TODO fix this!

    # Assume you're always providing power, with no battery storage.
    power_in = power_out

    # How much fuel do you burn?
    fuel_specific_energy = 43.02e6  # J/kg, for Jet A. Source: https://en.wikipedia.org/wiki/Jet_fuel#Jet_A
    gas_engine_efficiency = 0.15  # Fuel-to-alternator efficiency.
    # Taken from 3/5/20 propulsion team WAG in MIT 16.82. Numbers given: 24%/19%/15% for opti./med./consv. assumptions.

    fuel_burn_rate = power_in / fuel_specific_energy / gas_engine_efficiency  # kg/s

    mass_power_systems = mass_gas_engine + mass_alternator + mass_fuel

    # endregion
else:
    raise ValueError("Bad value of propulsion_type!")

# region Weights
# Payload mass
# mass_payload = # defined above

### Structural mass

# Wing
n_ribs_wing = 200 * opti.variable()
opti.set_initial(n_ribs_wing, 200)
opti.subject_to([
    n_ribs_wing > 0,
])
mass_wing_primary = lib_mass_struct.mass_wing_spar(
    span=wing.span(),
    mass_supported=max_mass_total,
    # technically the spar doesn't really have to support its own weight (since it's roughly spanloaded), so this is conservative
    ultimate_load_factor=structural_load_factor,
    n_booms=n_booms
)
mass_wing_secondary = lib_mass_struct.mass_hpa_wing(
    span=wing.span(),
    chord=wing.mean_geometric_chord(),
    vehicle_mass=max_mass_total,
    n_ribs=n_ribs_wing,
    n_wing_sections=1,
    ultimate_load_factor=structural_load_factor,
    t_over_c=0.14,
    include_spar=False,
)
mass_wing = mass_wing_primary + mass_wing_secondary

# mass_wing = lib_mass_struct.mass_hpa_wing(
#     span=wing.span(),
#     chord=wing.mean_geometric_chord(),
#     vehicle_mass=max_mass_total,
#     n_ribs=n_ribs_wing,
#     n_wing_sections=1,
#     ultimate_load_factor=structural_load_factor,
#     t_over_c=0.12,
#     include_spar=True,
# )

# Stabilizers
q_maneuver = 80  # TODO make this more accurate

n_ribs_hstab = 30 * opti.variable()
opti.set_initial(n_ribs_hstab, 40)
opti.subject_to([
    n_ribs_hstab > 0
])
mass_hstab_primary = lib_mass_struct.mass_wing_spar(
    span=hstab.span(),
    mass_supported=q_maneuver * 1.5 * hstab.area() / 9.81,
    ultimate_load_factor=structural_load_factor
)

mass_hstab_secondary = lib_mass_struct.mass_hpa_stabilizer(
    span=hstab.span(),
    chord=hstab.mean_geometric_chord(),
    dynamic_pressure_at_manuever_speed=q_maneuver,
    n_ribs=n_ribs_hstab,
    t_over_c=0.08,
    include_spar=False
)

mass_hstab = mass_hstab_primary + mass_hstab_secondary  # per hstab

n_ribs_vstab = 20 * opti.variable()
opti.set_initial(n_ribs_vstab, 35)
opti.subject_to([
    n_ribs_vstab > 0
])
mass_vstab_primary = lib_mass_struct.mass_wing_spar(
    span=vstab.span(),
    mass_supported=q_maneuver * 1.5 * vstab.area() / 9.81,
    ultimate_load_factor=structural_load_factor
) * 1.2  # TODO due to asymmetry, a guess
mass_vstab_secondary = lib_mass_struct.mass_hpa_stabilizer(
    span=vstab.span(),
    chord=vstab.mean_geometric_chord(),
    dynamic_pressure_at_manuever_speed=q_maneuver,
    n_ribs=n_ribs_vstab,
    t_over_c=0.08
)
mass_vstab = mass_vstab_primary + mass_vstab_secondary  # per vstab

# Fuselage & Boom
mass_boom = lib_mass_struct.mass_hpa_tail_boom(
    length_tail_boom=boom_length - wing_x_quarter_chord,  # support up to the quarter-chord
    dynamic_pressure_at_manuever_speed=q_maneuver,
    mean_tail_surface_area=hstab.area() + vstab.area()
)  # per boom

# The following taken from Daedalus:  # taken from Daedalus, http://journals.sfu.ca/ts/index.php/ts/article/viewFile/760/718
mass_fairings = 2.067
mass_landing_gear = 0.728

mass_fuse = mass_boom + mass_fairings + mass_landing_gear  # per fuselage

mass_structural = mass_wing + n_booms * (mass_hstab + mass_vstab + mass_fuse)
mass_structural *= structural_mass_margin_multiplier

### Avionics
# mass_avionics = 3.7 / 3.8 * 25  # back-calculated from Kevin Uleck's figures in MIT 16.82 presentation
mass_avionics = 3.179  # Pulled from Avionics team spreadsheet on 4/10
# https://docs.google.com/spreadsheets/d/1nhz2SAcj4uplEZKqQWHYhApjsZvV9hme9DlaVmPca0w/edit?pli=1#gid=0

opti.subject_to([
    mass_total / 500 == (
        mass_payload + mass_structural + mass_propulsion + mass_power_systems + mass_avionics
    ) / 500
    # 1 == (
    #         mass_payload + mass_structural + mass_propulsion + mass_power_systems + mass_avionics
    # ) * structural_mass_margin_multiplier / mass_total
    # 1 == (
    #         mass_payload + mass_structural + mass_propulsion + mass_power_systems + mass_avionics
    # ) / mass_total
])

gravity_force = g * mass_total

# endregion

# region Dynamics

net_force_parallel_calc = (
        thrust_force * cosd(alpha) -
        drag_force -
        gravity_force * sind(flight_path_angle)
)
net_force_perpendicular_calc = (
        thrust_force * sind(alpha) +
        lift_force -
        gravity_force * cosd(flight_path_angle)
)

opti.subject_to([
    net_accel_parallel / 1e-2 == net_force_parallel_calc / mass_total / 1e-2,
    net_accel_perpendicular / 1e-1 == net_force_perpendicular_calc / mass_total / 1e-1,
])

speeddot = net_accel_parallel
gammadot = (net_accel_perpendicular / airspeed) * 180 / np.pi

trapz = lambda x: (x[1:] + x[:-1]) / 2

dt = cas.diff(time)
dx = cas.diff(x)
dy = cas.diff(y)
dspeed = cas.diff(airspeed)
dgamma = cas.diff(flight_path_angle)

xdot_trapz = trapz(airspeed * cosd(flight_path_angle))
ydot_trapz = trapz(airspeed * sind(flight_path_angle))
speeddot_trapz = trapz(speeddot)
gammadot_trapz = trapz(gammadot)

##### Winds

wind_speed = wind_speed_func(y)
wind_speed_midpoints = wind_speed_func(trapz(y))

# Total
opti.subject_to([
    dx / 1e4 == (xdot_trapz - wind_speed_midpoints) * dt / 1e4,
    dy / 1e2 == ydot_trapz * dt / 1e2,
    dspeed / 1e-1 == speeddot_trapz * dt / 1e-1,
    dgamma / 1e-2 == gammadot_trapz * dt / 1e-2,
])

# Powertrain-specific
if propulsion_type == "solar":
    opti.subject_to([
        net_power / 5e3 < (power_in - power_out) / 5e3,
    ])
    net_power_trapz = trapz(net_power)

    dbattery_stored_energy_nondim = cas.diff(battery_stored_energy_nondim)
    opti.subject_to([
        dbattery_stored_energy_nondim / 1e-2 < (net_power_trapz / battery_capacity) * dt / 1e-2,
    ])
    opti.subject_to([
        battery_stored_energy_nondim[-1] > battery_stored_energy_nondim[0],
    ])
elif propulsion_type == "gas":
    pass  # TODO finish
    dmass_fuel_nondim = cas.diff(mass_fuel_nondim)
    opti.subject_to([
        # dmass_fuel_nondim / 1e-2 == -(trapz(fuel_burn_rate) / mass_fuel_max) * dt / 1e-2
        cas.diff(mass_fuel) == -(trapz(fuel_burn_rate)) * dt
    ])
else:
    raise ValueError("Bad value of propulsion_type!")

# endregion

# region Finalize Optimization Problem
##### Add periodic constraints
if enforce_periodicity:
    opti.subject_to([
        x[-1] / 1e5 > (x[0] + days_to_simulate * required_headway_per_day) / 1e5,
        y[-1] / 1e4 > y[0] / 1e4,
        airspeed[-1] / 2e1 > airspeed[0] / 2e1,
        # flight_path_angle[-1] == flight_path_angle[0],
        # alpha[-1] == alpha[0],
        # thrust_force[-1] / 1e2 == thrust_force[0] / 1e2,
    ])

##### Add initial state constraints
opti.subject_to([  # Air Launch
    x[0] == 0,
])

##### Optional constraints
if not allow_trajectory_optimization:
    # Prevent altitude cycling
    #     y_fixed = min_altitude * opti.variable()
    #     opti.set_initial(y_fixed, min_altitude)
    #     opti.subject_to([
    #         y > y_fixed - 100,
    #         y < y_fixed + 100
    #     ])
    opti.subject_to([
        flight_path_angle / 100 == 0
    ])
    # Prevent groundspeed loss
    opti.subject_to([
        airspeed / 20 > wind_speed / 20
    ])

###### Climb Optimization Constraints
if climb_opt:
    opti.subject_to([y[0] == 0])
    opti.subject_to([
        x[-1] / 1e5 > (x[timesteps_of_last_day] + days_to_simulate * required_headway_per_day) / 1e5,
        y[-1] / 1e4 > y[timesteps_of_last_day] / 1e4,
        airspeed[-1] / 2e1 > airspeed[timesteps_of_last_day] / 2e1,
        flight_path_angle[-1] == flight_path_angle[timesteps_of_last_day],
        alpha[-1] == alpha[timesteps_of_last_day],
        thrust_force[-1] / 1e2 == thrust_force[timesteps_of_last_day] / 1e2,
    ])
    opti.subject_to([battery_stored_energy_nondim[0] == allowable_battery_depth_of_discharge])

##### Add objective
if minimize == "TOGW":
    objective = max_mass_total / 300
elif minimize == "span":
    objective = wing_span / 50
else:
    raise ValueError("Bad value of minimize!")

##### Extra constraints


##### Add tippers
things_to_slightly_minimize = (
        wing_span / 80
        + n_propellers / 1
        + propeller_diameter / 2
        + battery_capacity_watt_hours / 30000
        + solar_area_fraction / 0.5
)

# Dewiggle
penalty = 0
penalty_denominator = n_timesteps
penalty += cas.sum1(cas.diff(thrust_force / 100) ** 2) / penalty_denominator
penalty += cas.sum1(cas.diff(net_accel_parallel / 1e-1) ** 2) / penalty_denominator
penalty += cas.sum1(cas.diff(net_accel_perpendicular / 1e-1) ** 2) / penalty_denominator
penalty += cas.sum1(cas.diff(airspeed / 30) ** 2) / penalty_denominator
penalty += cas.sum1(cas.diff(flight_path_angle / 10) ** 2) / penalty_denominator
penalty += cas.sum1(cas.diff(alpha / 5) ** 2) / penalty_denominator

opti.minimize(
    objective
    + penalty
    + 1e-6 * things_to_slightly_minimize
)
# endregion

# region Solve
p_opts = {}
s_opts = {}
s_opts["max_iter"] = 1e6  # If you need to interrupt, just use ctrl+c
# s_opts["bound_frac"] = 0.5
# s_opts["bound_push"] = 0.5
# s_opts["slack_bound_frac"] = 0.5
# s_opts["slack_bound_push"] = 0.5
s_opts["mu_strategy"] = "adaptive"
# s_opts["mu_oracle"] = "quality-function"
# s_opts["quality_function_max_section_steps"] = 20
# s_opts["fixed_mu_oracle"] = "quality-function"
# s_opts["alpha_for_y"] = "min"
# s_opts["alpha_for_y"] = "primal-and-full"
# s_opts["watchdog_shortened_iter_trigger"] = 1
# s_opts["expect_infeasible_problem"]="yes"
# s_opts["start_with_resto"] = "yes"
# s_opts["required_infeasibility_reduction"] = 0.001
# s_opts["evaluate_orig_obj_at_resto_trial"] = "yes"
opti.solver('ipopt', p_opts, s_opts)

# endregion

if __name__ == "__main__":
    try:
        sol = opti.solve()
    except:
        sol = opti.debug

    if np.abs(sol.value(penalty / objective)) > 0.01:
        print("\nWARNING: high penalty term! P/O = %.3f\n" % sol.value(penalty / objective))

    # Find dusk and dawn
    try:
        si = sol.value(solar_flux_on_horizontal)
        dusk = np.argwhere(si[:round(len(si) / 2)] < 1)[0, 0]
        dawn = np.argwhere(si[round(len(si) / 2):] > 1)[0, 0] + round(len(si) / 2)
    except IndexError:
        print("Could not find dusk and dawn - you likely have a shorter-than-one-day mission.")

    # # region Text Postprocessing & Utilities
    # ##### Text output
    o = lambda x: print(
        "%s: %f" % (x, sol.value(eval(x))))  # A function to Output a scalar variable. Input a variable name as a string
    outs = lambda xs: [o(x) for x in xs] and None  # input a list of variable names as strings
    print_title = lambda s: print("\n********** %s **********" % s.upper())

    print_title("Key Results")
    outs([
        "max_mass_total",
        "wing_span",
        "wing_root_chord"
    ])


    def qp(var_name):
        # QuickPlot a variable.
        fig = px.scatter(y=sol.value(eval(var_name)), title=var_name, labels={'y': var_name})
        fig.data[0].update(mode='markers+lines')
        fig.show()


    def qp2(x_name, y_name):
        # QuickPlot two variables.
        fig = px.scatter(
            x=sol.value(eval(x_name)),
            y=sol.value(eval(y_name)),
            title="%s vs. %s" % (x_name, y_name),
            labels={'x': x_name, 'y': y_name}
        )
        fig.data[0].update(mode='markers+lines')
        fig.show()


    def qp3(x_name, y_name, z_name):
        # QuickPlot two variables.
        fig = px.scatter_3d(
            x=sol.value(eval(x_name)),
            y=sol.value(eval(y_name)),
            z=sol.value(eval(z_name)),
            title="%s vs. %s" % (x_name, y_name),
            labels={'x': x_name, 'y': y_name},
            size_max=18
        )
        fig.data[0].update(mode='markers+lines')
        fig.show()


    s = lambda x: sol.value(x)

    draw = lambda: airplane.substitute_solution(sol).draw()


    # endregion

    # Draw mass breakdown
    def draw_pie():
        import matplotlib.pyplot as plt
        import matplotlib.style as style
        import plotly.express as px
        import plotly.graph_objects as go
        import dash
        import seaborn as sns

        sns.set(font_scale=1)
        pie_labels = [
            "Payload",
            "Structural",
            "Propulsion",
            "Power Systems",
            "Avionics"
        ]
        pie_values = [
            sol.value(mass_payload),
            sol.value(mass_structural),
            sol.value(mass_propulsion),
            sol.value(cas.mmax(mass_power_systems)),
            sol.value(mass_avionics),
        ]
        colors = plt.cm.Set2(np.arange(5))
        plt.pie(pie_values, labels=pie_labels, autopct='%1.1f%%', colors=colors)
        plt.title("Mass Breakdown at Takeoff")
        plt.show()


    draw_pie()

    # Write a mass budget
    with open("mass_budget.csv", "w+") as f:
        from types import ModuleType

        var_names = dir()
        f.write("Object or Collection of Objects, Mass [kg],\n")
        for var_name in var_names:
            if "mass" in var_name and not type(eval(var_name)) == ModuleType:
                f.write("%s, %f,\n" % (var_name, s(eval(var_name))))
