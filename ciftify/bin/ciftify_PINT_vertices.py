#!/usr/bin/env python
"""
Beta version of script find PINT (Personal Instrisic Network Topolography)

Usage:
  ciftify_PINT_vertices [options] <func.dtseries.nii> <left-surface.gii> <right-surface.gii> <input-vertices.csv> <outputprefix>

Arguments:
    <func.dtseries.nii>    Paths to directory source image
    <left-surface.gii>     Path to template for the ROIs of network regions
    <right-surface.gii>    Surface file .surf.gii to read coordinates from
    <input-vertices.csv>   Table of template vertices from which to Start
    <outputprefix>         Output csv file

Options:
  --pcorr                Use maximize partial correlation within network
                         (instead of pearson).
  --outputall            Output vertices from each iteration.
  --sampling-radius MM   Radius [default: 6] in mm of sampling rois
  --search-radius MM     Radius [default: 6] in mm of search rois
  --padding-radius MM    Radius [default: 12] in mm for min distance between roi centers
  --roi-limits ROIFILE   To limit rois, input a 4D dscalar file with one roi per roiidx
  -v,--verbose           Verbose logging
  --debug                Debug logging in Erin's very verbose style
  -h,--help              Print help

DETAILS
TBA

Written by Erin W Dickie, April 2016
"""
import random
import os
import sys
import logging
import pandas as pd
import nibabel.gifti.giftiio
from scipy import stats, linalg
import numpy as np
import nibabel as nib
from docopt import docopt

import ciftify

# Read logging.conf
logger = logging.getLogger('ciftify')
logger.setLevel(logging.DEBUG)

############################## maub starts here ######################
def run_PINT(arguments, tmpdir):
    global RADIUS_SAMPLING
    global RADIUS_SEARCH
    global RADIUS_PADDING

    func          = arguments['<func.dtseries.nii>']
    surfL         = arguments['<left-surface.gii>']
    surfR         = arguments['<right-surface.gii>']
    origcsv       = arguments['<input-vertices.csv>']
    output_prefix = arguments['<outputprefix>']
    pcorr         = arguments['--pcorr']
    outputall     = arguments['--outputall']
    RADIUS_SAMPLING = arguments['--sampling-radius']
    RADIUS_SEARCH = arguments['--search-radius']
    RADIUS_PADDING = arguments['--padding-radius']
    roi_limits_file = arguments['--roi-limits']

    logger.debug(arguments)

    logger.info("Arguments: ")
    logger.info('    functional data: {}'.format(func))
    logger.info('    left surface: {}'.format(surfL))
    logger.info('    right surface: {}'.format(surfR))
    logger.info('    pint template csv: {}'.format(origcsv))
    logger.info('    output prefix: {}'.format(output_prefix))
    logger.info('    Sampling ROI radius (mm): {}'.format(RADIUS_SAMPLING))
    logger.info('    Search ROI radius (mm): {}'.format(RADIUS_SEARCH))
    logger.info('    Paddding ROI radius (mm): {}'.format(RADIUS_PADDING))
    if pcorr:
        logger.info('    Maximizing partial correlation')
    else:
        logger.info('    Maximizing full correlation')
    if roi_limits_file:
        logger.info('\nLimiting vertices to the ROIs given in {}'.roi_limits_file)

    logger.info(ciftify.utils.section_header('Starting PINT'))

    ## loading the dataframe
    df = pd.read_csv(origcsv)
    if 'roiidx' not in df.columns:
        df.loc[:,'roiidx'] = pd.Series(np.arange(1,len(df.index)+1), index=df.index)

    ## load the func data
    func_dataL, func_dataR = ciftify.io.load_surfaces(func, suppress_echo = True)
    num_Lverts = func_dataL.shape[0]
    func_data = np.vstack((func_dataL, func_dataR))
    func_zeros = np.where(func_data[:,5]<5)[0]

    ## cp the surfaces to the tmpdir - this will cut down on i-o is tmpdir is ramdisk
    tmp_surfL = os.path.join(tmpdir, 'surface.L.surf.gii')
    tmp_surfR = os.path.join(tmpdir, 'surface.R.surf.gii')
    docmd(['cp', surfL, tmp_surfL])
    docmd(['cp', surfR ,tmp_surfR])
    surfL = tmp_surfL
    surfR = tmp_surfR

    ## run the main iteration
    df, max_distance, distance_outcol, iter_num = iterate_pint(df, 'tvertex', func_data,
                                                                func_zeros, pcorr,
                                                                surfL, surfR, num_Lverts)

    ## if roi limits file is given... then test to see if any of the vertices are outside the roi limits
    if roi_limits_file:
        ## read in the limits file
        limitsL, limitsR = ciftify.io.load_surfaces(roi_limits_file, suppress_echo = True)
        num_limLverts = limitsL.shape[0]
        limits = np.vstack((limitsL, limitsR))
        ## check if the roi has wandered outside the ROI limits
        df.loc[:,'avertex'] = -99
        all_good = True
        for i in df.index.tolist():
            thisivertex = df.loc[i,'ivertex']
            if df.loc[i,'hemi']=='R':
                thisivertex = thisivertex + num_limLverts
            if limits[thisivertex,i] > 0:
                df.loc[i,'avertex'] = df.loc[i,'ivertex']
            else:
                logger.info('resetting roiidx {}'.format(df.loc[i,'roiidx']))
                df.loc[i,'avertex'] = df.loc[i,'tvertex']
                all_good = False
        # if an roi is outside the limits.. set that roi back to teh tvertex and re-start iterating..
        if not all_good:
            df, max_distance, distance_outcol, iter_num = iterate_pint(df, 'avertex',
                                                                        func_data, func_zeros,
                                                                        pcorr, num_Lverts,
                                                                        start_iter = 50)

    if outputall:
        cols_to_export = list(df.columns.values)
    else:
        cols_to_export = ['hemi','NETWORK','roiidx','tvertex','ivertex','distance']
        if max_distance > 1:
            cols_to_export.extend([distance_outcol, 'vertex_{}'.format(iter_num - 2)])

    df.to_csv('{}_summary.csv'.format(output_prefix), columns = cols_to_export, index = False)
    ## load the sampling data

    ## output the tvertex meants
    sampling_rois = rois_bilateral(df, 'tvertex', RADIUS_SAMPLING, surfL, surfR)
    sampling_rois[func_zeros] = 0
    calc_sampling_meants(func_data, sampling_rois,
    outputcsv_name="{}_tvertex_meants.csv".format(output_prefix))
    ## output the ivertex meants
    sampling_rois = rois_bilateral(df, 'ivertex', RADIUS_SAMPLING, surfL, surfR)
    sampling_rois[func_zeros] = 0
    calc_sampling_meants(func_data, sampling_rois,
    outputcsv_name="{}_ivertex_meants.csv".format(output_prefix))

    logger.info(ciftify.utils.section_header('Done'))

### Erin's little function for running things in the shell
def docmd(cmdlist):
    '''run command and echo it to debug log '''
    ciftify.utils.run(cmdlist, suppress_echo = True)

def pint_logo():
    ''' logo from ascii text with font fender'''
    logo = """
'||'''|, |''||''| '||\   ||` |''||''|
 ||   ||    ||     ||\\  ||     ||
 ||...|'    ||     || \\ ||     ||
 ||         ||     ||  \\||     ||
.||      |..||..| .||   \||.   .||.
"""
    return(logo)

def log_build_environment():
    '''print the running environment info to the logs (info)'''
    logger.info("{}---### Environment Settings ###---".format(os.linesep))
    logger.info("Username: {}".format(get_stdout(['whoami'], echo = False).replace(os.linesep,'')))
    logger.info(ciftify.config.system_info())
    logger.info(ciftify.config.ciftify_version(os.path.basename(__file__)))
    logger.info(ciftify.config.wb_command_version())
    logger.info("---### End of Environment Settings ###---{}".format(os.linesep))
## measuring distance

def calc_surf_distance(surf, orig_vertex, target_vertex, radius_search):
    '''
    uses wb_command -surface-geodesic-distance command to measure
    distance between two vertices on the surface
    '''
    if int(orig_vertex) == int(target_vertex):
        distance = 0
    else:
        distances = ciftify.io.get_surf_distances(surf, orig_vertex,
                                                         radius_search = radius_search,
                                                         suppress_echo = True)
        distance = distances[target_vertex,0]
    return(distance)

def calc_distance_column(df, orig_vertex_col, target_vertex_col,distance_outcol,
                         radius_search, surfL, surfR):
    df.loc[:,distance_outcol] = -99.9
    for idx in df.index.tolist():
        orig_vertex = df.loc[idx, orig_vertex_col]
        target_vertex = df.loc[idx, target_vertex_col]
        hemi = df.loc[idx,'hemi']
        if hemi == "L":
            df.loc[idx, distance_outcol] = calc_surf_distance(surfL, orig_vertex,
                                            target_vertex, radius_search)
        if hemi == "R":
            df.loc[idx, distance_outcol] = calc_surf_distance(surfR, orig_vertex,
                                            target_vertex, radius_search)
    return df

def roi_surf_data(df, vertex_colname, surf, hemisphere, roi_radius):
    '''
    uses wb_command -surface-geodesic-rois to build rois (3D files)
    then load and collasp that into 1D array
    '''
    ## right the L and R hemisphere vertices from the table out to temptxt
    with ciftify.utils.TempDir() as lil_tmpdir:
        ## write a temp vertex list file
        vertex_list = os.path.join(lil_tmpdir, 'vertex_list.txt')
        df.loc[df.hemi == hemisphere, vertex_colname].to_csv(vertex_list,sep='\n',index=False)

        ## from the temp text build - func masks and target masks
        roi_surf = os.path.join(lil_tmpdir,'roi_surf.func.gii')
        docmd(['wb_command', '-surface-geodesic-rois', surf,
            str(roi_radius),  vertex_list, roi_surf,
            '-overlap-logic', 'EXCLUDE'])
        rois_data = ciftify.io.load_gii_data(roi_surf)

    ## multiply by labels and reduce to 1 vector
    vlabels = df[df.hemi == hemisphere].roiidx.tolist()
    rois_data = np.multiply(rois_data, vlabels)
    rois_data1D = np.max(rois_data, axis=1)

    return rois_data1D

def rois_bilateral(df, vertex_colname, roi_radius, surfL, surfR):
    '''
    runs roi_surf_data for both surfaces and combines them to one numpy array
    '''
    rois_L = roi_surf_data(df, vertex_colname, surfL, 'L', roi_radius)
    rois_R = roi_surf_data(df, vertex_colname, surfR, 'R', roi_radius)
    rois = np.hstack((rois_L, rois_R))
    return rois

def calc_network_meants(sampling_meants, df):
    '''
    calculate the network mean timeseries from many sub rois
    '''
    netmeants = pd.DataFrame(np.nan,
                                index = list(range(sampling_meants.shape[1])),
                                columns = df['NETWORK'].unique())
    for network in netmeants.columns:
        netlabels = df[df.NETWORK == network].roiidx.tolist()
        netmeants.loc[:,network] = np.mean(sampling_meants[(np.array(netlabels)-1), :], axis=0)
    return netmeants

def calc_sampling_meants(func_data, sampling_roi_mask, outputcsv_name=None):
    '''
    output a np.arrary of the meants for every index in the sampling_roi_mask
    '''
    # init output vector
    rois = np.unique(sampling_roi_mask)[1:]
    out_data = np.zeros((len(rois), func_data.shape[1]))

    # get mean seed dataistic from each, append to output
    for i, roi in enumerate(rois):
        idx = np.where(sampling_roi_mask == roi)[0]
        out_data[i,:] = np.mean(func_data[idx, :], axis=0)

    ## if the outputfile argument was given, then output the file
    if outputcsv_name:
        np.savetxt(outputcsv_name, out_data, delimiter=",")

    return(out_data)


def partial_corr(X,Y,Z):
    """
    Partial Correlation in Python (clone of Matlab's partialcorr)
    But Returns only one partial correlation value.

    This uses the linear regression approach to compute the partial
    correlation (might be slow for a huge number of variables). The
    algorithm is detailed here:

        http://en.wikipedia.org/wiki/Partial_correlation#Using_linear_regression

    Taking X and Y two variables of interest and Z the matrix with all the variable minus {X, Y},
    the algorithm can be summarized as

        1) perform a normal linear least-squares regression with X as the target and Z as the predictor
        2) calculate the residuals in Step #1
        3) perform a normal linear least-squares regression with Y as the target and Z as the predictor
        4) calculate the residuals in Step #3
        5) calculate the correlation coefficient between the residuals from Steps #2 and #4;

    The result is the partial correlation between X and Y while controlling for the effect of Z

    Returns the sample linear partial correlation coefficient between X and Y controlling
    for Z.


    Parameters
    ----------
    X : vector (length n)
    Y : vector (length n)
    Z : array-like, shape (n, p) where p are the variables to control for


    Returns
    -------
    pcorr : float - partial correlation between X and Y controlling for Z

    Adapted from https://gist.github.com/fabianp/9396204419c7b638d38f
    to return one value instead of partial correlation matrix
    """

    ## regress covariates on both X and Y
    beta_x = linalg.lstsq(Z, X)[0]
    beta_y = linalg.lstsq(Z, Y)[0]

    ## take residuals of above regression
    res_x = X - Z.dot(beta_x)
    res_y = Y - Z.dot(beta_y)

    ## correlate the residuals to get partial corr
    pcorr = stats.pearsonr(res_x, res_y)[0]

    ## return the partial correlation
    return pcorr

def pint_move_vertex(df, idx, vertex_incol, vertex_outcol,
                     func_data, sampling_meants,
                     search_rois, padding_rois, pcorr,
                     num_Lverts, netmeants = None):
    '''
    move one vertex in the pint algorithm
    inputs:
      df: the result dataframe
      idx: this vertices row index
      sampling_meants: the meants matrix calculated from the sampling rois
      search_rois: the search rois (the extent of the search radius)
      padding_rois: the padding rois (the mask that prevent search spaces from overlapping)
      pcorr : wether or not to use partial corr
      netmeants: netmeants object if running pcorr (if set to None, regular correlation is run)
    '''
    vlabel = df.loc[idx,'roiidx']
    network = df.loc[idx,'NETWORK']
    hemi = df.loc[idx,'hemi']
    orig_vertex = df.loc[idx, vertex_incol]

    ## get the meants - excluding this roi from the network
    netlabels = list(set(df[df.NETWORK == network].roiidx.tolist()) - set([vlabel]))
    meants = np.mean(sampling_meants[(np.array(netlabels) - 1), :], axis=0)

    # the search space is the intersection of the search radius roi and the padding rois
    # (the padding rois creates and exclusion mask if rois are to close to one another)
    idx_search = np.where(search_rois == vlabel)[0]
    idx_pad = np.where(padding_rois == vlabel)[0]
    idx_mask = np.intersect1d(idx_search, idx_pad, assume_unique=True)

    # if there padding mask and the search mask have no overlap - size is 0
    # there is nowhere for this vertex to move to so return the orig vertex id
    if not idx_mask.size:
        df.loc[idx,vertex_outcol] = orig_vertex

    else:
        # create output array
        seed_corrs = np.zeros(func_data.shape[0]) - 1

        # loop through each time series, calculating r
        for i in np.arange(len(idx_mask)):
            if pcorr:
                o_networks = set(netmeants.columns.tolist()) - set([network])
                seed_corrs[idx_mask[i]] = partial_corr(meants,
                                          func_data[idx_mask[i], :],
                                          netmeants.loc[:,o_networks].as_matrix())
            else:
                seed_corrs[idx_mask[i]] = np.corrcoef(meants,
                                                      func_data[idx_mask[i], :])[0][1]
        ## record the vertex with the highest correlation in the mask
        peakvert = np.argmax(seed_corrs, axis=0)
        if hemi =='R': peakvert = peakvert - num_Lverts
        df.loc[idx,vertex_outcol] = peakvert

    ## return the df
    return df

def iterate_pint(df, vertex_incol, func_data, func_zeros, pcorr,
                 surfL, surfR, num_Lverts, start_iter = 0):
    '''
    The main bit of pint
    inputs:
      df : the summary dataframe
      vertex_incol: the name of the column to use as the template rois
      func_data: the numpy array of the data
      func_data_mask: a mask of non-zero values from the func data
      pcorr: wether or not to use partial correlation
    return the summary dataframe
    '''
    iter_num = start_iter
    max_distance = 10

    while iter_num < (start_iter + 50) and max_distance > 1:
        vertex_outcol = 'vertex_{}'.format(iter_num)
        distance_outcol = 'dist_{}'.format(iter_num)
        df.loc[:,vertex_outcol] = -999
        df.loc[:,distance_outcol] = -99.9

        ## load the sampling data
        sampling_rois = rois_bilateral(df, vertex_incol, RADIUS_SAMPLING, surfL, surfR)
        sampling_rois[func_zeros] = 0

        ## load the search data
        search_rois = rois_bilateral(df, vertex_incol, RADIUS_SEARCH, surfL, surfR)
        search_rois[func_zeros] = 0

        ## load the padding-radius data
        padding_rois = rois_bilateral(df, vertex_incol, RADIUS_PADDING, surfL, surfR)

        ## calculate the sampling meants array
        sampling_meants = calc_sampling_meants(func_data, sampling_rois)

        ## if we are doing partial corr create a matrix of the network
        if pcorr:
            netmeants = calc_network_meants(sampling_meants, df)
        else:
            netmeants = None

        ## run the pint_move_vertex function for each vertex
        thisorder = df.index.tolist()
        random.shuffle(thisorder)
        for idx in thisorder:
            df = pint_move_vertex(df, idx, vertex_incol, vertex_outcol,
                                  func_data, sampling_meants,
                                  search_rois, padding_rois, pcorr,
                                  num_Lverts, netmeants)

        ## calc the distances
        df = calc_distance_column(df, vertex_incol, vertex_outcol, distance_outcol, RADIUS_SEARCH, surfL, surfR)
        numNotDone = df.loc[df.loc[:,distance_outcol] > 0, 'roiidx'].count()

        ## print the max distance as things continue..
        max_distance = max(df[distance_outcol])
        logger.info('Iteration {} \tmax distance: {}\tVertices Moved: {}'.format(iter_num, max_distance, numNotDone))
        vertex_incol = vertex_outcol
        iter_num += 1

    ## calc a final distance column
    df.loc[:,"ivertex"] = df.loc[:,vertex_outcol]
    df  = calc_distance_column(df, 'tvertex', 'ivertex', 'distance', 150, surfL, surfR)

    ## return the df
    return df, max_distance, distance_outcol, iter_num

def main():
    arguments  = docopt(__doc__)
    verbose      = arguments['--verbose']
    debug        = arguments['--debug']
    output_prefix = arguments['<outputprefix>']

    ch = logging.StreamHandler()
    ch.setLevel(logging.WARNING)
    if verbose:
        ch.setLevel(logging.INFO)
    if debug:
        ch.setLevel(logging.DEBUG)

    formatter = logging.Formatter('%(message)s')

    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # Get settings, and add an extra handler for the subject log
    local_logpath = os.path.dirname(output_prefix)
    if not os.path.exists(local_logpath): os.mkdir(local_logpath)
    fh = logging.FileHandler('{}_pint.log'.format(output_prefix))
    fh.setLevel(logging.INFO)
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    logger.info(pint_logo())
    logger.info(ciftify.utils.section_header("Personalized Intrinsic Network Topolography (PINT)"))
    with ciftify.utils.TempDir() as tmpdir:
        ret = run_PINT(arguments, tmpdir)
    sys.exit(ret)


if __name__ == '__main__':
    main()
