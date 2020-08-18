# Created July 2020 by R.Kessler
#
# Improvements w.r.t. SALT2mu_fit.pl:
#  + if there is 1 and only 1 version per INPDIR, there is no
#    need to specify STRINGMATCH or STRINGMATCH_IGNORE.
#
#  + If no errors are detected, everything is gzipped by default.
#
#  + Added MERGE.LOG to monitor STATE(WAIT,RUN,DONE,FAIL) and to
#    monitor stats for DATA,BIASCOR,CCPRIOR. This MERGE.LOG
#    similar to that for sim and fit jobs.
#
#  + output from optional wfit is in YAML format for easier parsing.
#
#  + NSPLITRAN runs on all FITOPT & MUOPT ... beware of large N_JOB_TOT
#
# - - - - 
# Potential Upgrades:
#   read list of BIASCOR outdirs from fit process, and automatically 
#   fill in simfile_biascor arg. Same for simfile_ccprior.
#
# Issues:
#   Does anybody use FITJOBS_SUMMARY.DAT or FITJOBS_SUMMARY.LOG ??
#   Includes grepping results for alpha,beta,etc ... This info could
#   go into YAML output for easier parsing.
#
# - - - - - - - - - -


import os, sys, shutil, yaml, glob
import logging, coloredlogs
import datetime, time
import submit_util as util
import submit_translate as tr

from submit_params    import *
from submit_prog_base import Program

PREFIX_SALT2mu           = "SALT2mu"

# define FIT column to extract FIT VERSION for FIT-MERGE.LOG
COLNUM_FIT_MERGE_VERSION = 1  # same param in submit_prog_fit.py -> fragile

# define colums in BBC MERGE.LOG
COLNUM_BBC_MERGE_VERSION      = 1
COLNUM_BBC_MERGE_FITOPT       = 2
COLNUM_BBC_MERGE_MUOPT        = 3
COLNUM_BBC_MERGE_SPLITRAN     = -1
COLNUM_BBC_MERGE_NEVT_DATA    = 4
COLNUM_BBC_MERGE_NEVT_BIASCOR = 5
COLNUM_BBC_MERGE_NEVT_CCPRIOR = 6


# list used in wrapup, cleanup, and merge_reset
JOB_SUFFIX_TAR_LIST  = [ 'YAML', 'DONE', 'LOG'  ]
SUFFIX_MOVE_LIST = [ SUFFIX_FITRES, SUFFIX_M0DIF ]

PROGRAM_wfit = "wfit.exe"

#SPLITRAN_SUMMARY_FILE = "SPLITRAN_SUMMARY.FITRES"
SPLITRAN_SUMMARY_FILE = "BBC_SUMMARY_SPLITRAN.FITRES"

# - - - - - - - - - - - - - - - - - - -  -
class BBC(Program):
    def __init__(self, config_yaml):

        config_prep = {}
        config_prep['program'] = PROGRAM_NAME_BBC
        super().__init__(config_yaml, config_prep)

    def set_output_dir_name(self):
        CONFIG     = self.config_yaml['CONFIG']
        input_file = self.config_yaml['args'].input_file  # for msgerr
        msgerr     = []
        if 'OUTDIR' in CONFIG :
            output_dir_name = os.path.expandvars(CONFIG['OUTDIR'])
        else:
            msgerr.append(f"OUTDIR key missing in yaml-CONFIG")
            msgerr.append(f"Check {input_file}")
            log_assert(False,msgerr)

        return output_dir_name,SUBDIR_SCRIPTS_BBC

    def translate_input_file(self, legacy_input_file):
        logging.info(f"\n TRANSLATE LEGACY SALT2mu_fit INPUT FILE: " \
                     f"{legacy_input_file}")
        refac_input_file = (f"REFAC_{legacy_input_file}")
        tr.BBC_legacy_to_refac(legacy_input_file,refac_input_file)
        # end translate_input_file

    def submit_prepare_driver(self):
        print("")

        # store list of BBC MUOPTs
        self.bbc_prep_muopt_list()

        self.bbc_prep_splitran()

        # read/store version and fitopt info
        self.bbc_prep_version_list()

        # figure out which versions to combine and create sorted lists
        self.bbc_prep_version_match() 

        # convet 2D and 4D nested loops into 1D loops
        self.bbc_prep_index_lists()

        # copy & combine tables from INPDIR+ directories
        self.bbc_prep_combine_tables()

        self.bbc_prep_copy_files()

        logging.info("")
        #sys.exit("\n xxx DEBUG DIE in bbc prepare_driver")
        # end submit_prepare_driver

    def bbc_prep_version_list(self):
        # read/store list of versions for each INPDIR+.
        # Make sure it exists, along with MERGE.LOG and SUBMIT.INFO
        # Store list of versions, and list of FITOPTs for each version.

        msgerr = []
        CONFIG        = self.config_yaml['CONFIG']
        input_file    = self.config_yaml['args'].input_file 

        key = 'INPDIR+'
        if key not in CONFIG :
            msgerr.append(f"Missing require key = {key} under CONFIG block")
            msgerr.append(f"Check {input_file}")
            self.log_assert(False,msgerr)

        n_inpdir = len(CONFIG[key])
        inpdir_list         = [ ]
        inpdir_list_orig    = [ ]  # before expandvar
        version_list2d = [ ] * n_inpdir  # vs. inpdir, iver
        fitopt_table_list2d = [ ] * n_inpdir
        fitopt_num_list     = [ ]
        n_fitopt_list       = [ ]
        n_version_list      = [ ]
        idir = 0; 

        for path_orig in CONFIG[key] :            
            logging.info(f"  Prepare INPDIR {path_orig}")
            path_expand        = os.path.expandvars(path_orig)
            MERGE_LOG_PATHFILE = (f"{path_expand}/{MERGE_LOG_FILE}")
            INFO_PATHFILE      = (f"{path_expand}/{SUBMIT_INFO_FILE}")
            DONE_PATHFILE      = (f"{path_expand}/{DEFAULT_DONE_FILE}")

            # check that required files exist
            msgerr = [f"Missing required {DEFAULT_DONE_FILE} file in", 
                      f"{path_orig}" ] 
            self.check_file_exists(DONE_PATHFILE,msgerr)

            msgerr = [f"Missing required {MERGE_LOG_FILE} file in", 
                      f"{path_orig}" ] 
            self.check_file_exists(MERGE_LOG_PATHFILE,msgerr)

            msgerr = [f"Missing required {SUBMIT_INFO_FILE} file in", 
                      f"{path_orig}" ] 
            self.check_file_exists(INFO_PATHFILE,msgerr)

            #  make sure DONE stamp exists with SUCCESS
            with open(DONE_PATHFILE,'r') as f :
                word = f.readlines();  word = word[0].rstrip("\n")
                if word != STRING_SUCCESS :
                    msgerr = []
                    msgerr.append(f"Expecting {STRING_SUCCESS} string written in ")
                    msgerr.append(f"   {DONE_PATHFILE}")
                    msgerr.append(f"but found {word} instead.")
                    msgerr.append(f"BBC cannot process FAILED LCFIT output.")
                    self.log_assert(False,msgerr)

            # read MERGE LOG file
            MERGE_INFO,comment_lines = util.read_merge_file(MERGE_LOG_PATHFILE)
            row_list = MERGE_INFO[TABLE_MERGE]
            version_list = []
            for row in row_list :
                version = row[COLNUM_FIT_MERGE_VERSION] 
                if version not in version_list :
                    version_list.append(version)

            # read FITOPT table from FIT job's submit info file
            fit_info_yaml = util.extract_yaml(INFO_PATHFILE)
            fitopt_table  = fit_info_yaml['FITOPT_LIST']
            n_fitopt      = len(fitopt_table)

            # udpates lists vs. idir
            n_fitopt_list.append(n_fitopt)
            fitopt_table_list2d.append(fitopt_table) 
            inpdir_list.append(path_expand)
            inpdir_list_orig.append(path_orig)
            version_list2d.append(version_list)
            n_version_list.append(len(version_list))

            idir += 1
            #print(f" xxx ------------------------------------------")
            #print(f" xxx version_list = {version_list} \n xxx in {path} ") 
            #print(f" xxx fitopt_list({n_fitopt}) = {fitopt_table}")

        # - - - -
        # strip off fitopt_num_list from fitopt_table
        for ifit in range(0,n_fitopt) :
            fitopt_table   = fitopt_table_list2d[0][ifit]
            fitopt_num     = fitopt_table[COLNUM_FITOPT_NUM]
            fitopt_num_list.append(fitopt_num)

        # - - - - -
        # abort if n_fitopt is different for any INPDIR
        if len(set(n_fitopt_list)) != 1 :
            msgerr = []
            msgerr.append(f"Mis-match number of FITOPT; "\
                          f"n_fitopt = {n_fitopt_list} for") 
            for path in inpdir_list_orig :
                msgerr.append(f"\t {path}")
            self.log_assert(False,msgerr)

        # store the goodies
        self.config_prep['n_inpdir']        = n_inpdir
        self.config_prep['inpdir_list']     = inpdir_list
        self.config_prep['version_list2d']  = version_list2d    # vs. idir,iver
        self.config_prep['n_version_list']  = n_version_list
        self.config_prep['n_fitopt']        = n_fitopt_list[0]  # per idir
        self.config_prep['fitopt_table_list2d'] = fitopt_table_list2d
        self.config_prep['fitopt_num_list']     = fitopt_num_list

        # end bbc_prep_version_list

    def bbc_prep_version_match(self):
        # using input IGNORE_STRING to figure out which version in each
        # inpdir to combine with other INPDIRs. Do not assume versions
        # are in same order in each INPDIR because some INPDIRs may
        # have extra test versions that are not relevant.
        # Beware, nasty logic !

        CONFIG           = self.config_yaml['CONFIG']
        n_inpdir         = self.config_prep['n_inpdir']
        n_fitopt         = self.config_prep['n_fitopt']
        inpdir_list      = self.config_prep['inpdir_list']
        n_version_list   = self.config_prep['n_version_list']
        version_list2d   = self.config_prep['version_list2d']

        msgerr = []
        key    = 'STRINGMATCH_IGNORE'

        # check if there is 1 and only 1 in every inpdir
        all_one = len(set(n_version_list)) == 1 and n_version_list[0] == 1
        
        # if STRINGMATCH is not defined, then there must be
        # 1 and only one version in each inpdir ... if not, abort.
        if key in CONFIG :
            stringmatch_ignore = CONFIG[key].split()
        else:
            stringmatch_ignore = [ 'IGNORE' ]

        if stringmatch_ignore[0] == 'IGNORE' :
            if all_one :
                # store original arrays as sorted since there is nothing to sort
                self.config_prep['n_version_out']            = 1
                self.config_prep['version_orig_sort_list2d'] = version_list2d
                self.config_prep['version_out_sort_list2d']  = version_list2d
                return
            else :
                msgerr.append(f"Only one VERSION per INPDIR allowed because {key}")
                msgerr.append(f"is not defined (or is set to IGNORE) in CONFIG block.")
                msgerr.append(f"n_version_list = {n_version_list} for ")
                msgerr += inpdir_list
                self.log_assert(False,msgerr)
        # - - - - -
        logging.info(f" STRINGMATCH_IGNORE = {stringmatch_ignore} \n")

        # start by removing the stringmatch_ignore from every version
        # Versions that have nothing replaced are tossed.

        version_orig_list2d = [] * n_inpdir  # version_list minus unmatched versions
        version_out_list2d = [] * n_inpdir 
        n_version_out_list = []  
        isort_list2d       = [] * n_inpdir 

        for idir in range(0,n_inpdir) :
            n_version = n_version_list[idir]
            #print(f" xxx ------ idir={idir} ------------ ")
            version_out_list  = []
            version_orig_list = []
            for iver in range(0,n_version) :
                version_orig = version_list2d[idir][iver]
                version_out  = version_orig
                for str_ignore in stringmatch_ignore :
                    version_out = version_out.replace(str_ignore,"")

                if version_out != version_orig :
                    version_out_list.append(version_out)
                    version_orig_list.append(version_orig)
                    #print(f" xxx version = {version_orig} -> {version_out} ")

            version_orig_list2d.append(version_orig_list)
            version_out_list2d.append(sorted(version_out_list))
            n_version_out_list.append(len(version_out_list))

            # store sort-map in isort_list2d
            x_list = sorted((e,i) for i,e in enumerate(version_out_list))
            isort_list = []
            for v,isort in x_list :
                isort_list.append(isort)
                #print(f"\t xxx   v = {v}  isort = {isort} ")
            isort_list2d.append(isort_list)
            # end idir loop over INPDIR+

        # - - - - - 
        # make sure that number of string-replaced version_out is the same 
        # in each inpdir, otherwise it's hopeless -> abort.
        same = len(set(n_version_out_list)) == 1 
        if not same :
            msgerr = []
            msgerr.append(f"Problem applying with STRINGMATCH_IGNORE.")
            msgerr.append(f"n_version_out_list = {n_version_out_list} ")
            msgerr.append(f"has different number of versions in each INPDIR.")
            self.log_assert(False,msgerr)
            
        # create sorted list of versions to combine
        n_version_out      = n_version_out_list[0] # Nvers with string replace
        version_orig_sort_list2d = \
            [['' for i in range(n_version_out)] for j in range(n_inpdir)]
        version_out_sort_list2d = \
            [['' for i in range(n_version_out)] for j in range(n_inpdir)]
        version_out_list = [] * n_version_out  # 1D array of output versions

        for iver in range(0,n_version_out):
            v_out  = version_out_list2d[0][iver]
            version_out_list.append(v_out)
            #print(f" xxx --------------- iver={iver} ------------------ ")
            for idir in range(0,n_inpdir):
                isort  = isort_list2d[idir][iver]
                v_orig = version_orig_list2d[idir][isort]
                v_out  = version_out_list2d[idir][iver]
                version_orig_sort_list2d[idir][isort] = v_orig
                version_out_sort_list2d[idir][isort]  = v_out
                #print(f" xxx idir,iver={idir},{iver}; {v_orig}->{v_out}")

        self.config_prep['n_version_out']            = n_version_out
        self.config_prep['version_out_list']         = version_out_list
        self.config_prep['version_orig_sort_list2d'] = version_orig_sort_list2d
        self.config_prep['version_out_sort_list2d']  = version_out_sort_list2d

        # end bbc_prep_version_match

    def bbc_prep_index_lists(self):
        # construct sparse 1D lists to loop over version and FITOPT

        CONFIG        = self.config_yaml['CONFIG']
        n_version     = self.config_prep['n_version_out']  
        n_fitopt      = self.config_prep['n_fitopt']
        n_muopt       = self.config_prep['n_muopt']
        n_splitran    = self.config_prep['n_splitran']

        n2d_index = n_version * n_fitopt
        n4d_index = n_version * n_fitopt * n_muopt * n_splitran

        # create 2D index lists used to prepare inputs
        # (create subdirs, catenate input FITRES files)
        iver_list2=[]; ifit_list2=[]; 
        for iver in range(0,n_version):
            for ifit in range(0,n_fitopt):
                iver_list2.append(iver)
                ifit_list2.append(ifit)

        # 4D lists are for prep_JOB_INFO
        iver_list4=[]; ifit_list4=[]; imu_list4=[]; isplitran_list4=[]
        for iver in range(0,n_version):
            for ifit in range(0,n_fitopt):
                for imu in range(0,n_muopt):
                    for isplitran in range(0,n_splitran):
                        iver_list4.append(iver)
                        ifit_list4.append(ifit)
                        imu_list4.append(imu)
                        isplitran_list4.append(isplitran+1) # 1-n_splitran

        self.config_prep['n2d_index']  = n2d_index
        self.config_prep['iver_list2'] = iver_list2
        self.config_prep['ifit_list2'] = ifit_list2

        self.config_prep['n4d_index']  = n4d_index
        self.config_prep['iver_list4'] = iver_list4
        self.config_prep['ifit_list4'] = ifit_list4
        self.config_prep['imu_list4']  = imu_list4
        self.config_prep['isplitran_list4']  = isplitran_list4

        # end bbc_prep_index_lists

    def bbc_prep_combine_tables(self):
        # create subdir for each version_out (after stringmatch_ignore)
        # catenate FITRES files from INPDIR+ so that each copied
        # FITRES file includes multiple surveys

        output_dir      = self.config_prep['output_dir']  
        n_version       = self.config_prep['n_version_out']  
        n_inpdir        = self.config_prep['n_inpdir']  
        v_orig_list     = self.config_prep['version_orig_sort_list2d']
        v_out_list      = self.config_prep['version_out_sort_list2d']
        n_fitopt        = self.config_prep['n_fitopt']
        inpdir_list     = self.config_prep['inpdir_list']
        fitopt_num_list = self.config_prep['fitopt_num_list']

        CONFIG        = self.config_yaml['CONFIG']
        OUTDIR        = CONFIG['OUTDIR']
        input_file    = self.config_yaml['args'].input_file 

        n2d_index  = self.config_prep['n2d_index']
        iver_list2 = self.config_prep['iver_list2'] 
        ifit_list2 = self.config_prep['ifit_list2']

        for i2d in range(0,n2d_index):
            iver  = iver_list2[i2d]
            ifit  = ifit_list2[i2d]
            idir0 = 0  # some things just need first INPDIR index

            # create version-output directory on first INPDIR
            if ifit == 0 :
                v_dir   = v_out_list[idir0][iver]
                V_DIR   = (f"{output_dir}/{v_dir}")
                logging.info(f"  Create output dir {OUTDIR}/{v_dir} ")
                os.mkdir(V_DIR)

            cat_list   = self.make_cat_fitres_list(iver,ifit)

            # execute the FITRES catenation
            fitopt_num     = fitopt_num_list[ifit]
            ff             = (f"{fitopt_num}.{SUFFIX_FITRES}")
            input_ff       = "INPUT_" + ff
            cat_file_out   = (f"{V_DIR}/{input_ff}")
            nrow = self.exec_cat_fitres(cat_list, cat_file_out)
            logging.info(f"\t Catenate {n_inpdir} {ff} files"\
                         f" -> {nrow} events ")

        logging.info("   gzip the catenated FITRES files.")
        vout_list  = self.config_prep['version_out_list']
        script_dir = self.config_prep['script_dir']
        for vout in vout_list :
            vout_dir = (f"{output_dir}/{vout}")
            cmd_gzip = (f"cd {vout_dir}; gzip INPUT_FITOPT*.{SUFFIX_FITRES}")
            os.system(cmd_gzip)

        # end bbc_prep_combine_tables
    
        
    def exec_cat_fitres(self,cat_list, cat_file_out):

        # prepare & execute catenate command for this cat_list 
        # (comma-sep list of files) into cat_file_out.
        # Use the "SALT2mu.exe cat_only" option to handle different
        # columns in each FITRES file. While .gz extensions are
        # not included, SALT2mu handles files with or without
        # .gz extensions.
        #
        # function returns number of rows in catenated file

        cat_file_log = "cat_FITRES_SALT2mu.LOG"

        cmd_cat = (f"SALT2mu.exe  " \
                   f"cat_only  "    \
                   f"datafile={cat_list}  " \
                   f"append_varname_missing='PROB*'  " \
                   f"catfile_out={cat_file_out}  " \
                   f" > {cat_file_log}"   )

        #print(f" xxx command to cat fitres files, \n {cmd_cat} \n")
        os.system(cmd_cat)

        # check number of rows
        nrow = util.nrow_table_TEXT(cat_file_out, "SN:")
        return nrow

        # end exec_cat_fitres

    def make_cat_fitres_list(self,iver, ifit ):
        
        # Use input indices for version (iver) and fitopt (ifit)
        # to construct comma-sep list of fitres files over INPDIRs.
        # This list is used to catenate FITRES files from different
        # input directories

        n_inpdir        = self.config_prep['n_inpdir']  
        v_orig_list     = self.config_prep['version_orig_sort_list2d']
        inpdir_list     = self.config_prep['inpdir_list']
        fitopt_num_list = self.config_prep['fitopt_num_list'] 
        # xxx fitopt_table_list2d = self.config_prep['fitopt_table_list2d']

        cat_list = ''
        fitopt_num   = fitopt_num_list[ifit]
        for idir in range(0,n_inpdir):
            inpdir       = inpdir_list[idir]
            v_orig       = v_orig_list[idir][iver]
            ff    = (f"{v_orig}/{fitopt_num}.{SUFFIX_FITRES}")
            FF    = (f"{inpdir}/{ff}")
            cat_list += (f"{FF},")
        cat_list = cat_list.rstrip(",")  # remove trailing comma
        return cat_list

        # end make_cat_fitres_list

    def bbc_prep_muopt_list(self):
        
        CONFIG        = self.config_yaml['CONFIG']
        input_file    = self.config_yaml['args'].input_file 
        n_muopt        = 1
        muopt_arg_list = [ '' ]  # always include MUOPT000 with no overrides
        muopt_num_list = [ 'MUOPT000' ] 
        key = 'MUOPT'
        if key in CONFIG :
            for muopt in CONFIG[key] :
                num = (f"MUOPT{n_muopt:03d}")
                muopt_arg_list.append(muopt)
                muopt_num_list.append(num)
                n_muopt += 1
                
        logging.info(f" Store {n_muopt-1} BBC options from MUOPT keys")

        self.config_prep['n_muopt']        = n_muopt
        self.config_prep['muopt_arg_list'] = muopt_arg_list
        self.config_prep['muopt_num_list'] = muopt_num_list

        # end bbc_prep_muopt_list

    def bbc_prep_splitran(self) :

        CONFIG        = self.config_yaml['CONFIG']

        # check NSPLITRAN option to fit random sub-samples.
        # Running on sim data, this feature is useful to measure
        # rms on cosmo params, and compare with fitted error.
        key_nsplitran = 'NSPLITRAN'
        n_splitran = 1
        if key_nsplitran in CONFIG : n_splitran = CONFIG[key_nsplitran]

        # squeeze in another splitran column
        self.add_COLNUM_BBC_MERGE_SPLITRAN(n_splitran)

        self.config_prep['n_splitran']  = n_splitran

        # end bbc_prep_splitran

    def add_COLNUM_BBC_MERGE_SPLITRAN(self,n_splitran):
        # if n_splitran > 1, need to squeeze in another MERGE.LOG column
        if n_splitran == 1 : return
        global COLNUM_BBC_MERGE_SPLITRAN
        global COLNUM_BBC_MERGE_NEVT_DATA
        global COLNUM_BBC_MERGE_NEVT_BIASCOR
        global COLNUM_BBC_MERGE_NEVT_CCPRIOR

        COLNUM_BBC_MERGE_SPLITRAN      = COLNUM_BBC_MERGE_MUOPT + 1
        COLNUM_BBC_MERGE_NEVT_DATA    += 1
        COLNUM_BBC_MERGE_NEVT_BIASCOR += 1
        COLNUM_BBC_MERGE_NEVT_CCPRIOR += 1

        # end adjust_COLNUM_BBC_MERGE

    def bbc_prep_copy_files(self):
        input_file    = self.config_yaml['args'].input_file 
        script_dir = self.config_prep['script_dir']
        shutil.copy(input_file,script_dir)
        # end prep_bbc_copy_files

    def write_command_file(self, icpu, COMMAND_FILE):

        input_file      = self.config_yaml['args'].input_file 
        n_version       = self.config_prep['n_version_out']  
        n_fitopt        = self.config_prep['n_fitopt']
        n_muopt         = self.config_prep['n_muopt']
        n_splitran      = self.config_prep['n_splitran']
        muopt_arg_list  = self.config_prep['muopt_arg_list']
        n_core          = self.config_prep['n_core']

        n4d_index        = self.config_prep['n4d_index']
        iver_list4       = self.config_prep['iver_list4'] 
        ifit_list4       = self.config_prep['ifit_list4']
        imu_list4        = self.config_prep['imu_list4']
        isplitran_list4  = self.config_prep['isplitran_list4']

        CONFIG   = self.config_yaml['CONFIG']
        use_wfit = 'WFITMUDIF_OPT' in CONFIG  # check follow-up job after bbc

        n_job_tot   = n_version * n_fitopt * n_muopt * n_splitran
        n_job_split = 1     # cannot break up BBC job as with sim or fit

        self.config_prep['n_job_split'] = n_job_split
        self.config_prep['n_job_tot']   = n_job_tot
        self.config_prep['use_wfit']    = use_wfit

        # open CMD file for this icpu  
        f = open(COMMAND_FILE, 'a')

        n_job_local = 0

        for i4d in range(0,n4d_index):
            iver      = iver_list4[i4d]
            ifit      = ifit_list4[i4d]
            imu       = imu_list4[i4d]
            isplitran = isplitran_list4[i4d]

            n_job_local += 1
            index_dict = \
                { 'iver':iver, 'ifit':ifit, 'imu':imu, 'icpu':icpu,
                  'isplitran': isplitran }

            if ( (n_job_local-1) % n_core ) == icpu :
                last_job   = (n_job_tot - n_job_local) < n_core            

                job_info_bbc   = self.prep_JOB_INFO_bbc(index_dict)
                util.write_job_info(f, job_info_bbc, icpu)

                if use_wfit :
                    job_info_wfit  = self.prep_JOB_INFO_wfit(index_dict)
                    util.write_job_info(f, job_info_wfit, icpu)

                job_info_merge = self.prep_JOB_INFO_merge(icpu,last_job) 
                util.write_jobmerge_info(f, job_info_merge, icpu)

                # write JOB_INFO to file f

        f.close()

        # end write_command_file

    def prep_JOB_INFO_bbc(self,index_dict):
        # Return JOB_INFO dictionary with 
        #   cd job_dir
        #   program.exe arg_list  > log_file
        #   touch TMP_[xxx].DONE
        #
        # Inputs
        #   index_dict = dictionary of indices for this job
        #
        # Beware that input FITRES files are not in script_dir,
        # but they are in ../[version]

        # strip off indices from input dictionary
        iver      = index_dict['iver']
        ifit      = index_dict['ifit']
        imu       = index_dict['imu'] 
        isplitran = index_dict['isplitran'] 

        #print(f" xxx iver={iver}, ifit={ifit}, imu={imu} ", \
            #flush=True)

        input_file  = self.config_yaml['args'].input_file 
        fast        = self.config_yaml['args'].fast

        program     = self.config_prep['program']
        output_dir  = self.config_prep['output_dir']
        script_dir  = self.config_prep['script_dir']
        version     = self.config_prep['version_out_list'][iver]
        fitopt_num  = self.config_prep['fitopt_num_list'][ifit] # e.g FITOPT002
        muopt_num   = self.config_prep['muopt_num_list'][imu] # e.g MUOPT003
        muopt_arg   = self.config_prep['muopt_arg_list'][imu]
        n_splitran  = self.config_prep['n_splitran']
        use_wfit    = self.config_prep['use_wfit']

        # construct row mimicking MERGE.LOG
        row         = [ None, version, fitopt_num, muopt_num, isplitran ]
        prefix_orig, prefix_final = self.bbc_prefix("bbc", row)
        input_ff    = (f"INPUT_{fitopt_num}.{SUFFIX_FITRES}") 

        JOB_INFO = {}
        JOB_INFO['program']     = program
        JOB_INFO['input_file']  = input_file
        JOB_INFO['job_dir']     = script_dir
        JOB_INFO['log_file']    = (f"{prefix_orig}.LOG")
        JOB_INFO['done_file']   = (f"{prefix_orig}.DONE")

        # if wfit job will run, suppress DONE file here and wait for
        # wfit to finish before writing DONE files. This logic avoids
        # confusing the merge process where SALT2mu has finished but
        # wfit still runs.
        if use_wfit : JOB_INFO['done_file'] = ''

        arg_list = []
        arg_list.append(f"  prefix={prefix_orig}")
        arg_list.append(f"  datafile=../{version}/{input_ff}")
        arg_list.append(f"  write_yaml=1")

        if n_splitran > 1 :
            # note that fortran-like isplitran index is used here
            arg = (f"NSPLITRAN={n_splitran} JOBID_SPLITRAN={isplitran}")
            arg_list.append(f"  {arg}")

        arg_list.append(f"{muopt_arg}")     # user input

        # check command line input --fast option to prescale by 10
        # Only sim is pre-scaled; don't pre-scale data.
        if fast:
            arg_list.append(f"prescale_simdata={FASTFAC}")

        JOB_INFO['arg_list'] = arg_list

        return JOB_INFO

        # end prep_JOB_INFO_bbc


    def prep_JOB_INFO_wfit(self,index_dict):
        # optional: run wfit cosmology fitter if WFITMUDIF_OPT is set in CONFIG

        iver      = index_dict['iver']
        ifit      = index_dict['ifit']
        imu       = index_dict['imu'] 
        isplitran = index_dict['isplitran'] 

        CONFIG        = self.config_yaml['CONFIG']
        output_dir    = self.config_prep['output_dir']
        script_dir    = self.config_prep['script_dir']
        version       = self.config_prep['version_out_list'][iver]
        fitopt_num    = self.config_prep['fitopt_num_list'][ifit] 
        muopt_num     = self.config_prep['muopt_num_list'][imu] # e.g MUOPT003

        row         = [ None, version, fitopt_num, muopt_num, isplitran ]

        prefix_bbc_orig,  prefix_bbc_final  = self.bbc_prefix("bbc",  row)
        prefix_wfit_orig, prefix_wfit_final = self.bbc_prefix("wfit", row)
    
        # note that the done file has the SALT2mu/BBC done stamp,
        # not a wfit done stamp.
        wfit_inp_file   = (f"{prefix_bbc_orig}.{SUFFIX_M0DIF}")
        wfit_done_file  = (f"{prefix_bbc_orig}.DONE")  
        wfit_out_file   = (f"{prefix_wfit_orig}.YAML")
        wfit_log_file   = (f"{prefix_wfit_orig}.LOG")

        arg_list = []
        arg_list.append(f"-cospar_yaml {wfit_out_file} ") 
        arg_list.append(CONFIG['WFITMUDIF_OPT'])

        JOB_INFO = {}
        JOB_INFO['program']     = PROGRAM_wfit
        JOB_INFO['input_file']  = wfit_inp_file
        JOB_INFO['log_file']    = wfit_log_file
        JOB_INFO['done_file']   = wfit_done_file
        JOB_INFO['job_dir']     = ""  # same job dir as SALT2mu.exe job
        JOB_INFO['arg_list']    = arg_list

        return JOB_INFO

        # prep_JOB_INFO_wfit 

    def append_info_file(self,f):
        # append info to SUBMIT.INFO file

        vout_list         = self.config_prep['version_out_list']
        n_version         = self.config_prep['n_version_out']
        n_fitopt          = self.config_prep['n_fitopt']
        n_muopt           = self.config_prep['n_muopt']
        muopt_arg_list    = self.config_prep['muopt_arg_list']
        muopt_num_list    = self.config_prep['muopt_num_list']

        n_splitran        = self.config_prep['n_splitran']
        use_wfit          = self.config_prep['use_wfit']
        
        f.write(f"\n# BBC info\n")

        # beware that LOG,DONE,YAML files are not under script_dir,
        # but under ../[VERSION]
        f.write(f"JOBFILE_WILDCARD:  '*FITOPT*MUOPT*' \n")

        f.write(f"NVERSION:   {n_version}      " \
                f"# number of data VERSIONs\n")
        f.write(f"NFITOPT:    {n_fitopt}      " \
                f"# number of FITOPTs\n")
        f.write(f"NMUOPT:     {n_muopt}      " \
                f"# number of BBC options\n")
        f.write(f"NSPLITRAN:  {n_splitran}      " \
                f"# number of random sub-samples\n")
        f.write(f"USE_WFIT:   {use_wfit}     " \
                f"# option to run wfit on BBC output\n")

        f.write("\n")
        f.write("VERSION_OUT_LIST:\n")
        for v in vout_list :
            f.write(f"  - {v}\n")

        f.write("\n")
        f.write("MUOPT_LIST: \n")
        for imu in range(0,n_muopt):
            num   = muopt_num_list[imu]
            label = None             # later need to allow labels ??
            arg   = muopt_arg_list[imu]
            row   = [ num, label, arg ]
            f.write(f"  - {row} \n")
        f.write("\n")

        # end append_info_file

    def create_merge_table(self,f):

        n4d_index   = self.config_prep['n4d_index']
        iver_list4  = self.config_prep['iver_list4'] 
        ifit_list4  = self.config_prep['ifit_list4']
        imu_list4   = self.config_prep['imu_list4']
        isplitran_list4 = self.config_prep['isplitran_list4']
        n_splitran  = self.config_prep['n_splitran']

        # create only MERGE table ... no need for SPLIT table

        header_splitran = ""
        if n_splitran > 1 :   header_splitran = "SPLITRAN  "

        header_line_merge = \
            (f" STATE   VERSION  FITOPT  MUOPT {header_splitran}" \
             f"NEVT_DATA  NEVT_BIASCOR  NEVT_CCPRIOR " )

        INFO_MERGE = { 
            'primary_key' : TABLE_MERGE, 'header_line' : header_line_merge,
            'row_list'    : []   }

        STATE = SUBMIT_STATE_WAIT # all start in WAIT state
        for i4d in range(0,n4d_index):
            iver      = iver_list4[i4d]
            ifit      = ifit_list4[i4d]
            imu       = imu_list4[i4d]
            isplitran = isplitran_list4[i4d]

            version    = self.config_prep['version_out_list'][iver]
            fitopt_num = (f"FITOPT{ifit:03d}")
            muopt_num  = (f"MUOPT{imu:03d}")

            # ROW here is fragile in case columns are changed
            ROW_MERGE = []
            ROW_MERGE.append(STATE)
            ROW_MERGE.append(version)
            ROW_MERGE.append(fitopt_num)
            ROW_MERGE.append(muopt_num)
            if n_splitran > 1 : ROW_MERGE.append(isplitran)
            ROW_MERGE.append(0)    # NEVT_DATA
            ROW_MERGE.append(0)    # NEVT_BIASCOR
            ROW_MERGE.append(0)    # NEVT_CCPRIOR

            INFO_MERGE['row_list'].append(ROW_MERGE)  
        util.write_merge_file(f, INFO_MERGE, [] ) 

        # end create_merge_table

    def merge_config_prep(self,output_dir):

        submit_info_yaml = self.config_prep['submit_info_yaml']
        vout_list   = submit_info_yaml['VERSION_OUT_LIST']
        n_fitopt    = submit_info_yaml['NFITOPT']
        n_muopt     = submit_info_yaml['NMUOPT']
        n_splitran  = submit_info_yaml['NSPLITRAN']

        self.config_prep['version_out_list'] = vout_list
        self.config_prep['n_splitran']       = n_splitran
        self.add_COLNUM_BBC_MERGE_SPLITRAN(n_splitran)

        # end merge_config_prep

    def merge_update_state(self, MERGE_INFO_CONTENTS):

        # read MERGE.LOG, check LOG & DONE files.
        # Return update row list MERGE tables.

        submit_info_yaml = self.config_prep['submit_info_yaml']
        output_dir       = self.config_prep['output_dir']
        script_dir       = submit_info_yaml['SCRIPT_DIR']
        n_job_split      = submit_info_yaml['N_JOB_SPLIT']
        
        COLNUM_STATE     = COLNUM_MERGE_STATE
        COLNUM_VERSION   = COLNUM_BBC_MERGE_VERSION
        COLNUM_FITOPT    = COLNUM_BBC_MERGE_FITOPT  
        COLNUM_MUOPT     = COLNUM_BBC_MERGE_MUOPT 
        COLNUM_NDATA     = COLNUM_BBC_MERGE_NEVT_DATA
        COLNUM_NBIASCOR  = COLNUM_BBC_MERGE_NEVT_BIASCOR
        COLNUM_NCCPRIOR  = COLNUM_BBC_MERGE_NEVT_CCPRIOR

        row_list_merge   = MERGE_INFO_CONTENTS[TABLE_MERGE]

        # init outputs of function
        n_state_change     = 0
        row_list_merge_new = []

        nrow_check = 0
        for row in row_list_merge :
            row_list_merge_new.append(row) # default output is same as input
            nrow_check += 1
            irow        = nrow_check - 1 # row index

            # strip off row info
            STATE       = row[COLNUM_STATE]

            prefix_orig, prefix_final = self.bbc_prefix("bbc", row)
            search_wildcard = (f"{prefix_orig}*")

            # check if DONE or FAIL ; i.e., if Finished
            Finished = (STATE == SUBMIT_STATE_DONE) or \
                       (STATE == SUBMIT_STATE_FAIL)

            if not Finished :
                NEW_STATE = STATE

                # get list of LOG, DONE, and YAML files 
                log_list, done_list, yaml_list = \
                    util.get_file_lists_wildcard(script_dir,search_wildcard)

                # careful to sum only the files that are NOT None
                NLOG   = sum(x is not None for x in log_list)  
                NDONE  = sum(x is not None for x in done_list)  
                NYAML  = sum(x is not None for x in yaml_list)  

                if NLOG > 0:
                    NEW_STATE = SUBMIT_STATE_RUN
                if NDONE == n_job_split :
                    NEW_STATE=SUBMIT_STATE_DONE
                    bbc_stats=self.get_bbc_stats(script_dir,log_list,yaml_list)
                    
                    # check for failures in snlc_fit jobs.
                    nfail = bbc_stats['nfail_sum']
                    if nfail > 0 :
                        NEW_STATE = SUBMIT_STATE_FAIL
                 
                # update row if state has changed
                if NEW_STATE != STATE :
                    row[COLNUM_STATE]     = NEW_STATE
                    row[COLNUM_NDATA]     = bbc_stats['nevt_data']
                    row[COLNUM_NBIASCOR]  = bbc_stats['nevt_biascor']
                    row[COLNUM_NCCPRIOR]  = bbc_stats['nevt_ccprior']
                    
                    row_list_merge_new[irow] = row  # update new row
                    n_state_change += 1             # assume nevt changes

        # - - - - - -  -
        # The first return arg (row_split) is null since there is 
        # no need for a SPLIT table
        return [], row_list_merge_new, n_state_change

        # end merge_update_state

    def get_bbc_stats(self, search_dir, log_list, yaml_list):
        submit_info_yaml = self.config_prep['submit_info_yaml']
        n_log_file       = len(log_list)
        split_stats = {
            'nevt_data'           : 0, 
            'nevt_biascor'        : 0,
            'nevt_ccprior'        : 0,
            'nfail_sum'           : 0
        }
        
        for isplit in range(0,n_log_file):            
            yaml_file = yaml_list[isplit]            
            nevt_test = -9        # used to search for failures
            if yaml_file :
                YAML_FILE = (f"{search_dir}/{yaml_file}")
                yaml_data = util.extract_yaml(YAML_FILE)
                split_stats['nevt_data']     += yaml_data['NEVT_DATA']
                split_stats['nevt_biascor']  += yaml_data['NEVT_BIASCOR']
                split_stats['nevt_ccprior']  += yaml_data['NEVT_CCPRIOR']

                # test value for failure testing below
                nevt_test = yaml_data['ABORT_IF_ZERO'] 

            # check flag to check for failure.        
            if nevt_test <= 0 :
                log_file   = log_list[isplit]
                found_fail = self.check_for_failure(log_file,nevt_test,isplit+1)
                if found_fail :
                    split_stats['nfail_sum'] += 1

        return split_stats

        # end get_bbc_stats
        
    def merge_job_wrapup(self, irow, MERGE_INFO_CONTENTS):

        # For irow of MERGE.LOG,
        # copy ouput FITRES and M0DIFF files to their final ../VERSION
        # location, and remove VERSION_ from the file names since
        # they are under VERSION/subdir.
        # Example:
        #  TEST_FITOPT000_MUOPT000.FITRES is moved to 
        #  ../TEST/FITOPT000_MUOPT000.FITRES
        # ff means fitres file.

        submit_info_yaml = self.config_prep['submit_info_yaml']
        output_dir       = self.config_prep['output_dir']
        script_dir       = submit_info_yaml['SCRIPT_DIR']
        use_wfit         = submit_info_yaml['USE_WFIT']

        row   = MERGE_INFO_CONTENTS[TABLE_MERGE][irow]
        version = row[COLNUM_BBC_MERGE_VERSION]
        prefix_orig, prefix_final = self.bbc_prefix("bbc", row)

        cddir         = (f"cd {script_dir}")
        cdv           = (f"cd {output_dir}/{version}")

        logging.info(f"\t Move {prefix_orig} files to {version}/ ")
        for suffix_move in SUFFIX_MOVE_LIST :
            orig_file = (f"{prefix_orig}.{suffix_move}")
            move_file = (f"{prefix_final}.{suffix_move}")
            cmd_move  = (f"{cddir}; mv {orig_file} ../{version}/{move_file}")
            cmd_gzip  = (f"gzip ../{version}/{move_file}")
            cmd_all   = (f"{cmd_move} ; {cmd_gzip}")
            #print(f" xxx cmd_all = {cmd_all}")
            os.system(cmd_all)

        # check to move wfit YAML file (don't bother gzipping)
        if use_wfit :
            prefix_orig, prefix_final = self.bbc_prefix("wfit", row)
            suffix_move = "YAML"
            orig_file = (f"{prefix_orig}.{suffix_move}")
            move_file = (f"{prefix_final}.{suffix_move}")
            cmd_move  = (f"{cddir}; mv {orig_file} ../{version}/{move_file}")
            cmd_all   = (f"{cmd_move}")
            os.system(cmd_all)

        if irow == 9999 :
            sys.exit("\n xxx DEBUG DIE xxx \n")

    #end merge_job_wrapup
    
        
    def merge_cleanup_final(self):

        # Every SALT2mu job succeeded, so here we simply compress output.

        output_dir       = self.config_prep['output_dir']
        submit_info_yaml = self.config_prep['submit_info_yaml']
        vout_list        = submit_info_yaml['VERSION_OUT_LIST']
        jobfile_wildcard = submit_info_yaml['JOBFILE_WILDCARD']
        script_dir       = submit_info_yaml['SCRIPT_DIR']
        n_splitran       = submit_info_yaml['NSPLITRAN']
        script_subdir    = SUBDIR_SCRIPTS_BBC

        if n_splitran > 1 :
            logging.info(f"  BBC cleanup: create {SPLITRAN_SUMMARY_FILE}")
            self.make_splitran_summary()
            #return   # xxxx REMOVE 

        logging.info(f"  BBC cleanup: compress {JOB_SUFFIX_TAR_LIST}")
        for suffix in JOB_SUFFIX_TAR_LIST :
            wildcard = (f"{jobfile_wildcard}*.{suffix}") 
            util.compress_files(+1, script_dir, wildcard, suffix )

        self.merge_cleanup_script_dir()

        # end merge_cleanup_final

    def make_splitran_summary(self):

        # collect all BBC fit params, and optional w(wfit);
        # write them out into a FITRES-formatted text file.
        # Include column indices for FITOPT and MUOPT.

        output_dir       = self.config_prep['output_dir']
        submit_info_yaml = self.config_prep['submit_info_yaml']
        script_dir       = submit_info_yaml['SCRIPT_DIR']
        use_wfit         = submit_info_yaml['USE_WFIT']
        vout_list        = submit_info_yaml['VERSION_OUT_LIST']

        SUMMARYF_FILE     = (f"{output_dir}/{SPLITRAN_SUMMARY_FILE}")
        f = open(SUMMARYF_FILE,"w") 

        self.write_splitran_comments(f)
        self.write_splitran_header(f)

        # read the whole MERGE.LOG file to figure out where things ae
        MERGE_LOG_PATHFILE  = (f"{output_dir}/{MERGE_LOG_FILE}")
        MERGE_INFO_CONTENTS,comment_lines = \
            util.read_merge_file(MERGE_LOG_PATHFILE)

        nrow = 0 
        for row in MERGE_INFO_CONTENTS[TABLE_MERGE]:
            version    = row[COLNUM_BBC_MERGE_VERSION] # sim data version
            fitopt_num = row[COLNUM_BBC_MERGE_FITOPT]  # e.g., FITOPT002
            muopt_num  = row[COLNUM_BBC_MERGE_MUOPT]   # e.g., MUOPT003
            isplitran  = row[COLNUM_BBC_MERGE_SPLITRAN]
            
            # get indices for summary file
            iver = vout_list.index(version)
            ifit = (f"{fitopt_num[6:]}")
            imu  = (f"{muopt_num[5:]}")

            # process all splitran files upon reaching SPLITRAN=1
            # in MERGE.LOG file
            if isplitran > 1 : continue
            nrow += 1  # for row number in summary file

            # the ugly code is in get_splitran_values
            varname_list,value_list2d = self.get_splitran_values(row)

            # for each list of values, get statistics, then print to table.
            n_var = len(varname_list)
            for ivar in range(0,n_var):
                varname    = varname_list[ivar]
                value_list = value_list2d[ivar]
                stat_dict  = util.get_stat_dict(value_list)
                AVG = stat_dict['AVG'] ;  ERR_AVG = stat_dict['ERR_AVG']
                RMS = stat_dict['RMS'] ;  ERR_RMS = stat_dict['ERR_RMS']
                f.write(f"ROW: {nrow:3d} {iver} {ifit} {imu} {varname:<10} " \
                        f"{AVG:8.4f} {ERR_AVG:8.4f} "\
                        f"{RMS:8.4f} {ERR_RMS:8.4f} \n") 

            f.write(f"\n")

            if nrow == 77777 : break  # debug only

        f.close()
        # end make_splitran_summary
    
    def get_splitran_values(self,row):

        # for input row from MERGE.LOG, return
        #   varnames_list (list of variables names with BBC results)
        #   values_list2d (list of values vs. splitran for each variable)

        output_dir       = self.config_prep['output_dir']
        submit_info_yaml = self.config_prep['submit_info_yaml']
        script_dir       = submit_info_yaml['SCRIPT_DIR']
        use_wfit         = submit_info_yaml['USE_WFIT']

        version         = row[COLNUM_BBC_MERGE_VERSION]
        prefix_orig,prefix_final = self.bbc_prefix("bbc", row)

        # scoop up YAML files. Be careful that '-{isplitran} is the
        # part we need to exlude from prefix, but we don't want to 
        # remove other dashes in version name.
        prefix_search = prefix_orig.rsplit('-',1)[0]  # remove isplitran number
        wildcard_yaml = (f"{prefix_search}*.YAML")
        yaml_list     = glob.glob1(script_dir, wildcard_yaml)

        bbc_results_yaml   = []
        for yaml_file in yaml_list :
            YAML_FILE = (f"{script_dir}/{yaml_file}")
            tmp_yaml  = util.extract_yaml(YAML_FILE)
            bbc_results_yaml.append(tmp_yaml)

        # Make list of varnames[ivar] and value_list2d[ivar][isplitran]
        # Trick is to convert [isplitran][ivar] -> [ivar][isplitran]
        #   (I hate this code)
        n_var = len(bbc_results_yaml)
        if use_wfit :  n_var += 1 ;  
        
        varname_list = []
        value_list2d = [ 0.0 ] * n_var  # [ivar][isplitran]
        for ivar in range(0,n_var): value_list2d[ivar] = []
        isplitran    = 0
        
        for results in bbc_results_yaml:  # loop over splitran
            BBCFIT_RESULTS = results['BBCFIT_RESULTS']
            #print(f"\n xxx BBCFIT_RESULTS = {BBCFIT_RESULTS}")
            ivar = 0 
            for item in BBCFIT_RESULTS:  # loop over variables
                for key,val in item.items() :
                    str_val = str(val).split()[0]
                    value_list2d[ivar].append(float(str_val))
                    if isplitran == 0 : varname_list.append(key)
                ivar += 1
            isplitran += 1

        # - - - - - - - - 
        # check option to include w(wfit)
        # Note that wfit_*YAML files have already been moved to 
        # ../version and had version string removed from file name;
        # therefore, use prefix_final instead of prefix_orig

        if use_wfit :
            ivar = n_var - 1
            w_list = [] ; 
            varname_list.append("w_wfit")

            prefix_orig,prefix_final = self.bbc_prefix("wfit", row)
            prefix_search = prefix_final.rsplit('-',1)[0] 
            wildcard_yaml = (f"{prefix_search}*.YAML")
            
            v_dir         = (f"{output_dir}/{version}")
            yaml_list     = glob.glob1(v_dir, wildcard_yaml)

            for yaml_file in yaml_list :
                YAML_FILE = (f"{v_dir}/{yaml_file}")
                tmp_yaml  = util.extract_yaml(YAML_FILE)
                w         = tmp_yaml['w']
                w_list.append(w)
            value_list2d[ivar] = w_list

        return varname_list, value_list2d

        # end get_splitran_values

    def write_splitran_comments(self, f):

        output_dir       = self.config_prep['output_dir']
        submit_info_yaml = self.config_prep['submit_info_yaml']
        script_dir       = submit_info_yaml['SCRIPT_DIR']
        use_wfit         = submit_info_yaml['USE_WFIT']
        vout_list        = submit_info_yaml['VERSION_OUT_LIST']
        muopt_list       = submit_info_yaml['MUOPT_LIST']
        n_splitran       = submit_info_yaml['NSPLITRAN']

        f.write(f"# ========================================= \n")
        f.write(f"# NSPLITRAN: {n_splitran} \n#\n")
        # write comments with map if IVER -> VERSION
        iver = 0
        for vout in vout_list :
            f.write(f"# IVER = {iver:2d} --> {vout} \n")
            iver += 1
        f.write(f"#\n")

        # element 0: FITOPTnnn
        # element 1: optional label
        # element 2: arg list
        for muopt in muopt_list :
            muopt_num = muopt[0]
            muopt_arg = muopt[2]
            f.write(f"# {muopt_num}: {muopt_arg} \n")

        f.write(f"#\n")
        f.write(f"# ERR_AVG = RMS/sqrt(NSNFIT) \n")
        f.write(f"# ERR_RMS = RMS/sqrt(2*NSNFIT) \n")
        f.write(f"# ========================================= \n\n")

        #end write_splitran_comments

    def write_splitran_header(self, f):
        # write header using names under BBCFIT_RESULTS
        f.write("VARNAMES: ROW IVER FITOPT MUOPT  FITPAR  " \
                f"AVG  ERR_AVG RMS  ERR_RMS \n")

    def merge_reset(self,output_dir):

        # unpack things in merge_cleanup_final, but in reverse order

        output_dir       = self.config_prep['output_dir']
        submit_info_yaml = self.config_prep['submit_info_yaml']
        vout_list        = submit_info_yaml['VERSION_OUT_LIST']
        jobfile_wildcard = submit_info_yaml['JOBFILE_WILDCARD']
        script_dir       = submit_info_yaml['SCRIPT_DIR']
        script_subdir    = SUBDIR_SCRIPTS_BBC
        fnam = "merge_reset"

        logging.info(f"   {fnam}: reset STATE and NEVT in {MERGE_LOG_FILE}")
        MERGE_LOG_PATHFILE = (f"{output_dir}/{MERGE_LOG_FILE}")
        colnum_zero_list = [ COLNUM_BBC_MERGE_NEVT_DATA, 
                             COLNUM_BBC_MERGE_NEVT_BIASCOR,
                             COLNUM_BBC_MERGE_NEVT_CCPRIOR ]
        util.merge_table_reset(MERGE_LOG_PATHFILE, TABLE_MERGE,  \
                               COLNUM_MERGE_STATE, colnum_zero_list)

        logging.info(f"  {fnam}: uncompress {script_subdir}/")
        util.compress_subdir(-1, f"{output_dir}/{script_subdir}" )

        logging.info(f"  {fnam}: uncompress {JOB_SUFFIX_TAR_LIST}")
        for suffix in JOB_SUFFIX_TAR_LIST :
            wildcard = (f"{jobfile_wildcard}*.{suffix}") 
            util.compress_files(-1, script_dir, wildcard, suffix )

        logging.info(f"  {fnam}: uncompress CPU* files")
        util.compress_files(-1, script_dir, "CPU*", "CPU" )

        logging.info(f"  {fnam}: restore {SUFFIX_MOVE_LIST} to {script_subdir}")
        for vout in vout_list : 
            vout_dir = (f"{output_dir}/{vout}")
            cdv      = (f"cd {vout_dir}")
            logging.info(f" \t\t restore {vout}")
            for suffix_move in SUFFIX_MOVE_LIST :
                #logging.info(f" \t\t restore {vout}/*{suffix_move}")
                wildcard  = (f"FITOPT*.{suffix_move}")
                cmd_unzip = (f"{cdv} ; gunzip {wildcard}.gz")
                os.system(cmd_unzip)

            # restore each file with version_ appended to name
            ff_list = sorted(glob.glob1(vout_dir,"FITOPT*"))
            #print(f"\t xxx ff_list = {ff_list} ")
            for ff in ff_list:
                ff_move   = (f"{script_dir}/{vout}_{ff}")                    
                cmd_move  = (f"mv {ff} {ff_move}")
                cmd_all   = (f"{cdv} ; {cmd_move}")
                os.system(cmd_all)

        # end merge_reset

    def bbc_prefix(self, program, row):

        # Input program can be
        #   SALT2mu or bbc -> nominal code
        #   wift           -> tack on extra 'wfit' to prefix
        #
        # Input row has the format of a row in MERGE.LOG
        #
        # Function returns 
        #    prefix_orig  (includes version_ )
        #    prefix_final (does not include version_

        version       = row[COLNUM_BBC_MERGE_VERSION]
        fitopt_num    = row[COLNUM_BBC_MERGE_FITOPT]
        muopt_num     = row[COLNUM_BBC_MERGE_MUOPT]

        prefix_orig   = (f"{version}_{fitopt_num}_{muopt_num}")
        prefix_final  = (f"{fitopt_num}_{muopt_num}")

        n_splitran    = self.config_prep['n_splitran']
        if n_splitran > 1 :
            isplitran      = row[COLNUM_BBC_MERGE_SPLITRAN]
            prefix_orig   += (f"-{isplitran:03d}")
            prefix_final  += (f"-{isplitran:03d}")

        # check for adding 'wfit' to prefix
        if program.lower() == 'wfit' :
            prefix_orig  = (f"wfit_{prefix_orig}")
            prefix_final = (f"wfit_{prefix_final}")

        return prefix_orig, prefix_final

        # end bbc_prefix


