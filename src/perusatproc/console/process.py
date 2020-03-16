# -*- coding: utf-8 -*-
"""
Given a path to a PeruSat-1 product, this script peforms all required steps to
form a single calibrated, orthorectified and pansharpened image.

Final output can be a virtual raster or a tiff file.

"""

import argparse
import sys
import logging

from perusatproc import __version__
from perusatproc import calibration, orthorectification, pansharpening
from perusatproc.util import run_command

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

DEFAULT_TILE_SIZE = 2**14


def process_image(*, src, dst):

    name, ext = os.path.splitext(os.path.basename(src))
    dirname = os.path.dirname(src)
    dim_xml = glob(os.path.join(dirname, 'DIM_*.XML'))[0]
    rpc_xml = glob(os.path.join(dirname, 'RPC_*.XML'))[0]

    os.makedirs(os.path.join(dst, 'calibarate'), exist_ok=True)
    os.makedirs(os.path.join(dst, 'orthorectify'), exist_ok=True)
    calibration_dst_path = os.path.join(dst, 'calibarate', name + ext)
    orthorectify_dst_path = os.path.join(dst, 'orthorectify', name + ext)

    calibration.calibrate(src_path=src,
                          dst_path=calibration_dst_path,
                          metadata_path=os.path.join(dirname, dim_xml))

    with tempfile.NamedTemporaryFile(suffix='.tif') as tempf:
        _logger.info("Add RPC tags from %s and write %s", calibration_dst_path,
                     tempf.name)
        orthorectification.add_rpc_tags(src_path=calibration_dst_path,
                                        dst_path=tempf.name,
                                        metadata_path=os.path.join(
                                            dirname, rpc_xml))

        _logger.info("Orthorectify %s and write %s", tempf.name,
                     orthorectify_dst_path)
        orthorectification.orthorectify(
            src_path=tempf.name,
            dst_path=orthorectify_dst_path,
        )


def retile_images(inputs, outdir, tile_size=DEFAULT_TILE_SIZE):
    cmd = 'gdal_retile.py -co COMPRESS=DEFLATE -co TILED=YES ' \
        '-ps {tile_size} {tile_size} ' \
        '-targetDir {outdir} ' \
        '{inputs}'.format(tile_size=tile_size,
        outdir=outdir,
        inputs=inputs.join(' '))


def process_product(src, dst, tile_size=DEFAULT_TILE_SIZE):
    volumes = glob(os.path.join(src, 'VOL_*'))
    _logger.info("Num. Volumes: {}".format(len(volumes)))

    os.makedirs(dst, exist_ok=True)
    os.makedirs(os.path.join(dst, 'pansharpening'), exist_ok=True)

    gdal_imgs = []

    for volume in volumes:
        ms_img = glob(os.path.join(volume, 'IMG_*_MS_*/*.TIF'))[0]
        p_img = glob(os.path.join(volume, 'IMG_*_P_*/*.TIF'))[0]
        process_image(src=ms_img, dst=volume)
        process_image(src=p_img, dst=volume)
        pansharpening_dst = os.path.join(
            dst, 'pansharpening', '{}.tif'.format(os.path.basename(volume)))
        pansharpening.pansharpen(inp=os.path.join(volume, 'orthorectify',
                                                  os.path.basename(p_img)),
                                 inxs=os.path.join(volume, 'orthorectify',
                                                   os.path.basename(ms_img)),
                                 out=pansharpening_dst)
        gdal_imgs.append(pansharpening_dst)

    retile_images(inputs=gdal_imgs, outdir=dst, tile_size=tile_size)


def parse_args(args):
    """Parse command line parameters

    Args:
      args ([str]): command line parameters as list of strings

    Returns:
      :obj:`argparse.Namespace`: command line parameters namespace
    """
    parser = argparse.DefaultsArgumentParser(
        description=
        "Process a PeruSat-1 product into a set of calibrated, orthorectified and pansharpened tiles",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)

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

    parser.add_argument("src", help="path to directory containing product")
    parser.add_argument("dst",
                        help="path to output directory containing tiles")

    parser.add_argument("--tile-size",
                        type=int,
                        default=DEFAULT_TILE_SIZE,
                        help="tile size (in pixels)")

    parser.add_argument("-co",
                        "--create-options",
                        default="",
                        help="GDAL create options")

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

    #if args.mode == 'image':
    #    process_image(args.src, args.dst, metadata=args.dst)
    #else:
    process_product(args.src, args.dst, tile_size=args.tile_size)


def run():
    """Entry point for console_scripts
    """
    main(sys.argv[1:])


if __name__ == "__main__":
    run()
