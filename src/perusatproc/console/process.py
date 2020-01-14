# -*- coding: utf-8 -*-
"""
This is a skeleton file that can serve as a starting point for a Python
console script. To run this script uncomment the following lines in the
[options.entry_points] section in setup.cfg:

    console_scripts =
         fibonacci = perusatproc.skeleton:run

Then run `python setup.py install` which will install the command `fibonacci`
inside your current environment.
Besides console scripts, the header (i.e. until _logger...) of this file can
also be used as template for Python modules.

Note: This skeleton file can be safely removed if not needed!
"""

import argparse
import sys
import logging

from perusatproc import __version__

import subprocess
import xmltodict
import tempfile
import rasterio
import os
from glob import glob
from datetime import datetime

__author__ = "Damián Silvani"
__copyright__ = "Dymaxion Labs"
__license__ = "mit"

_logger = logging.getLogger(__name__)


def run_command(cmd):
    _logger.info(cmd)
    subprocess.run(cmd, shell=True, check=True)


def extract_calibration_metadata(metadata_path):
    with open(metadata_path) as f:
        body = xmltodict.parse(f.read())

    doc = body['Dimap_Document']

    # Image date and time
    strip_source = doc['Dataset_Sources']['Source_Identification'][
        'Strip_Source']
    date_str, time_str = strip_source['IMAGING_DATE'], strip_source[
        'IMAGING_TIME']
    date = datetime.strptime(date_str, "%Y-%m-%d")
    time = datetime.strptime(time_str, "%H:%M:%S")

    geom_values = doc['Geometric_Data']['Use_Area']['Located_Geometric_Values']

    # Sun elevation and azimuth angles
    solar_incidences = geom_values['Solar_Incidences']
    sun_elev = float(solar_incidences['SUN_ELEVATION'])
    sun_azim = float(solar_incidences['SUN_AZIMUTH'])

    # Viewing elevation and azimuth angles
    acquisition_angles = geom_values['Acquisition_Angles']
    view_elev = float(acquisition_angles['VIEWING_ANGLE'])
    view_azim = float(acquisition_angles['AZIMUTH_ANGLE'])

    # Gains and biases for each band
    band_spectral_range = doc['Radiometric_Data']['Radiometric_Calibration'][
        'Instrument_Calibration']['Band_Measurement_List']
    band_radiances = band_spectral_range['Band_Radiance']
    if not isinstance(band_radiances, list):
        band_radiances = [band_radiances]
    gains = [float(r['GAIN']) for r in band_radiances]
    biases = [float(r['BIAS']) for r in band_radiances]

    # Solar irradiance values for each band
    band_solar_irradiances = band_spectral_range['Band_Solar_Irradiance']
    if not isinstance(band_solar_irradiances, list):
        band_solar_irradiances = [band_solar_irradiances]
    solar_irradiances = [float(r['VALUE']) for r in band_solar_irradiances]

    return dict(minute=time.minute,
                hour=time.hour,
                day=date.day,
                month=date.month,
                year=date.year,
                sun_elev=sun_elev,
                sun_azim=sun_azim,
                view_elev=view_elev,
                view_azim=view_azim,
                gains=gains,
                biases=biases,
                solar_irradiances=solar_irradiances)


def extract_projection_metadata(metadata_path):
    with open(metadata_path) as f:
        body = xmltodict.parse(f.read())

    doc = body['Dimap_Document']

    # Raster size
    raster_dimensions = doc['Raster_Data']['Raster_Dimensions']
    sizex = int(raster_dimensions['NCOLS'])
    sizey = int(raster_dimensions['NROWS'])

    # Raster extent
    vertices = doc['Dataset_Content']['Dataset_Extent']['Vertex']
    lats = [float(v['LAT']) for v in vertices]
    lons = [float(v['LON']) for v in vertices]
    minx, maxx = min(lons), max(lons)
    miny, maxy = min(lats), max(lats)

    return dict(sizex=sizex,
                sizey=sizey,
                ulx=minx,
                uly=maxy,
                lrx=maxx,
                lry=miny)


def calibrate(*, src_path, dst_path, metadata_path):
    base_cmd = """otbcli_OpticalCalibration \
      -in {src} \
      -out {dst} uint16 \
      -milli true \
      -level toa \
      -acqui.minute {minute} \
      -acqui.hour {hour} \
      -acqui.day {day} \
      -acqui.month {month} \
      -acqui.year {year} \
      -acqui.sun.elev {sun_elev} \
      -acqui.sun.azim {sun_azim} \
      -acqui.view.elev {view_elev} \
      -acqui.view.azim {view_azim} \
      -acqui.gainbias {gainbias_path} float \
      -acqui.solarilluminations {solarillum_path} float
    """
    metadata = extract_calibration_metadata(metadata_path)

    with tempfile.NamedTemporaryFile(suffix='.txt') as gf:
        for k in ('gains', 'biases'):
            line = "{}\n".format(" : ".join(str(v) for v in metadata[k]))
            gf.write(line.encode())
            gf.flush()

        with tempfile.NamedTemporaryFile(suffix='.txt') as sf:
            line = "{}\n".format(" : ".join(
                str(v) for v in metadata['solar_irradiances']))
            sf.write(line.encode())
            sf.flush()

            cmd = base_cmd.format(src=src_path,
                                  dst=dst_path,
                                  gainbias_path=gf.name,
                                  solarillum_path=sf.name,
                                  **metadata)
            run_command(cmd)


def reproject(*, src_path, dst_path, metadata_path):
    metadata = extract_projection_metadata(metadata_path)

    with rasterio.open(src_path) as ds:
        profile = ds.profile.copy()
        transform = rasterio.transform.from_bounds(west=metadata['ulx'],
                                                   south=metadata['lry'],
                                                   east=metadata['lrx'],
                                                   north=metadata['uly'],
                                                   width=metadata['sizex'],
                                                   height=metadata['sizey'])

        profile.update(transform=transform, crs='epsg:4326')
        with rasterio.open(dst_path, 'w', **profile) as wds:
            wds.write(ds.read())


def process_image(src, dst, metadata=None):
    if not metadata:
        _logger.info(
            "Metadata file not provided. Going to look for XML file in src image directory."
        )
        src_dirname = os.path.dirname(src)
        metadata_glob_path = os.path.join(src_dirname, 'DIM_*.XML')
        metadata_paths = glob(metadata_glob_path)
        if not metadata_paths:
            raise RuntimeError(
                'No metadata file found at {}. ' \
                'Please provide one with -m/--metadata.'.format(src_dirname))
        metadata = metadata_paths[0]

    _logger.info("Metadata file: {}".format(metadata))

    with tempfile.NamedTemporaryFile(suffix='.tif') as tempf:
        calibrate(src_path=src, dst_path=tempf.name, metadata_path=metadata)
        reproject(src_path=tempf.name, dst_path=dst, metadata_path=metadata)


def process_product(src, dst):
    volumes = glob(os.path.join(src, 'VOL_*'))
    _logger.info("Num. Volumes: {}".format(len(volumes)))

    os.makedirs(dst, exist_ok=True)

    for volume in volumes:
        ms_img = glob(os.path.join(volume, 'IMG_*_MS_*/*.TIF'))[0]
        p_img = glob(os.path.join(volume, 'IMG_*_P_*/*.TIF'))[0]

        for src_path in [ms_img]:
            name, ext = os.path.splitext(os.path.basename(src_path))
            dst_path = os.path.join(dst, name + ext)
            if not os.path.exists(dst_path):
                process_image(src=src_path, dst=dst_path)


def parse_args(args):
    """Parse command line parameters

    Args:
      args ([str]): command line parameters as list of strings

    Returns:
      :obj:`argparse.Namespace`: command line parameters namespace
    """
    parser = argparse.ArgumentParser(
        description=
        "Perform radiometric calibration from Level 2A to Top-of-Atmosphere (ToA)"
    )

    parser.add_argument("--version",
                        action="version",
                        version="perusatproc {ver}".format(ver=__version__))

    parser.add_argument("-v",
                        "--verbose",
                        dest="loglevel",
                        help="set loglevel to INFO",
                        action="store_const",
                        const=logging.INFO)
    parser.add_argument("-vv",
                        "--very-verbose",
                        dest="loglevel",
                        help="set loglevel to DEBUG",
                        action="store_const",
                        const=logging.DEBUG)

    subparsers = parser.add_subparsers(dest='mode', required=True)

    image_parser = subparsers.add_parser("image", help="calibrate an image")
    image_parser.add_argument("src", help="path to input image")
    image_parser.add_argument("dst", help="path to output image")
    image_parser.add_argument("-m",
                              "--metadata",
                              help="path to metadata XML file")

    product_parser = subparsers.add_parser("product",
                                           help="calibrate a product")
    product_parser.add_argument("src",
                                help="path to directory containing product")
    product_parser.add_argument("dst", help="path to output image")

    return parser.parse_args(args)


def setup_logging(loglevel):
    """Setup basic logging

    Args:
      loglevel (int): minimum loglevel for emitting messages
    """
    logformat = "[%(asctime)s] %(levelname)s:%(name)s:%(message)s"
    logging.basicConfig(level=loglevel,
                        stream=sys.stdout,
                        format=logformat,
                        datefmt="%Y-%m-%d %H:%M:%S")


def main(args):
    """Main entry point allowing external calls

    Args:
      args ([str]): command line parameter list
    """
    args = parse_args(args)
    setup_logging(args.loglevel)

    if args.mode == 'image':
        process_image(args.src, args.dst, metadata=args.dst)
    else:
        process_product(args.src, args.dst)


def run():
    """Entry point for console_scripts
    """
    main(sys.argv[1:])


if __name__ == "__main__":
    run()