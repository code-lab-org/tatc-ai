from tatc.schemas import Satellite, TwoLineElements, Instrument
from tatc.utils import swath_width_to_field_of_regard
from tatc.analysis import compute_ground_track
from tatc.utils.orbital import compute_ground_surface_velocity
from datetime import timedelta
import pandas as pd

orbit = TwoLineElements(tle=[
    "1 43013U 17073A   26022.94240312  .00000200  00000+0  11574-3 0  9998",
    "2 43013  98.7645 323.7982 0001179  28.0279 332.0961 14.19539020423863"])
altitude_m = orbit.get_altitude(); swath_width_m = 3000e3
instrument = Instrument(name="VIIRS", field_of_regard=swath_width_to_field_of_regard(altitude_m, swath_width_m))
satellite = Satellite(name="NOAA 20", orbit=orbit, instruments=[instrument])

start = orbit.get_epoch(); end = start + timedelta(minutes=30)
inclination_deg = orbit.get_inclination()
max_delta_t_s = swath_width_m / compute_ground_surface_velocity(altitude_m, inclination_deg)
times = pd.date_range(start, end, freq=timedelta(seconds=max_delta_t_s / 10))
ground_track = compute_ground_track(satellite, times)
RESULT = ground_track.iloc[0].geometry.wkt
