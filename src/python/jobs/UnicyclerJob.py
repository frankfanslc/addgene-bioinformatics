from argparse import ArgumentParser
import logging
import os
from pathlib import Path

from toil.job import Job
from toil.common import Toil
from toil.lib.docker import apiDockerCall

import utilities

logger = logging.getLogger(__name__)


class UnicyclerJob(Job):
    """
    Accepts paired-end Illumina reads for assembly using Unicycler.
    """

    def __init__(
        self,
        read_one_file_id,
        read_two_file_id,
        config_file_id,
        config_file_name,
        output_directory,
        parent_rv={},
        *args,
        **kwargs
    ):
        """
        Parameters
        ----------
        read_one_file_id : toil.fileStore.FileID
            id of the file in the file store containing FASTQ Illumina
            short left paired reads
        read_two_file_id : toil.fileStore.FileID
            id of the file in the file store containing FASTQ Illumina
            short right paired reads
        config_file_id : toil.fileStore.FileID
            id of the file in the file store containing assembler args
        config_file_name : str
            name of the file in the file store containing assembler args
        output_directory : str
            name of directory for output
        parent_rv : dict
            dictionary of return values from the parent job
        """
        super(UnicyclerJob, self).__init__(*args, **kwargs)
        self.read_one_file_id = read_one_file_id
        self.read_two_file_id = read_two_file_id
        self.config_file_id = config_file_id
        self.config_file_name = config_file_name
        self.output_directory = output_directory
        self.parent_rv = parent_rv

    def run(self, fileStore):
        """
        Returns
        -------
        dict of toil.fileStore.FileID and str
            file ids and names of log, and contigs FASTA file
        """
        # Expected output file names
        log_file_name = "unicycler.log"
        contigs_file_name = "assembly.fasta"
        graph_file_name = "assembly.gfa"

        try:
            # Read the config file from the file store into the local
            # temporary directory, and parse
            config_file_path = utilities.readGlobalFile(
                fileStore, self.config_file_id, self.config_file_name
            )
            common_config, assembler_params = utilities.parseConfigFile(
                config_file_path, "unicycler"
            )

            # Get BBNorm config for input path
            common_config, bbnorm_params = utilities.parseConfigFile(
                config_file_path, "bbnorm"
            )

            # temporary directory
            read_one_file_path = utilities.readGlobalFile(
                fileStore, self.read_one_file_id, bbnorm_params["read_one_file_name"]
            )
            read_two_file_path = utilities.readGlobalFile(
                fileStore, self.read_two_file_id, bbnorm_params["read_two_file_name"]
            )

            # Mount the Toil local temporary directory to the same path in
            # the container, and use the path as the working directory in
            # the container, then call Unicycler
            # TODO: Specify the container on construction
            image = "ralatsdio/unicycler:v0.4.8"
            working_dir = fileStore.localTempDir
            logger.info("Calling image {0}".format(image))
            parameters = [
                "unicycler",
                "--short1",
                read_one_file_path,
                "--short2",
                read_two_file_path,
                "--out",
                os.path.join(working_dir, self.output_directory),
            ]
            if len(assembler_params) > 0:
                parameters.extend(assembler_params)
            logger.info("Using parameters {0}".format(str(parameters)))
            apiDockerCall(
                self,
                image=image,
                volumes={working_dir: {"bind": working_dir, "mode": "rw"}},
                working_dir=working_dir,
                parameters=parameters,
            )

            # Write the Unicycler log, and final assembly contigs
            # FASTA and graph GFA v1 files from the local temporary
            # directory into the file store
            log_file_id = utilities.writeGlobalFile(
                fileStore, self.output_directory, log_file_name
            )
            contigs_file_id = utilities.writeGlobalFile(
                fileStore, self.output_directory, contigs_file_name
            )
            graph_file_id = utilities.writeGlobalFile(
                fileStore, self.output_directory, graph_file_name
            )

        except Exception as exc:
            # Ensure expectred return values on exceptions
            logger.info("Calling image {0} failed: {1}".format(image, exc))
            log_file_id = None
            contigs_file_id = None
            graph_file_id = None

        # Return file ids and names for export
        unicycler_rv = {
            "unicycler_rv": {
                "log_file": {
                    "id": log_file_id,
                    "name": log_file_name,
                },
                "contigs_file": {
                    "id": contigs_file_id,
                    "name": contigs_file_name,
                },
                "graph_file": {
                    "id": graph_file_id,
                    "name": graph_file_name,
                },
            }
        }
        unicycler_rv.update(self.parent_rv)
        logger.info("Return value {0}".format(unicycler_rv))
        return unicycler_rv


if __name__ == "__main__":
    """
    Assemble reads corresponding to a single well.
    """
    # Parse FASTQ data path, plate and well specification,
    # configuration path and file, and output directory, making the
    # output directory if needed
    parser = ArgumentParser()
    Job.Runner.addToilOptions(parser)
    cmps = str(os.path.abspath(__file__)).split(os.sep)[0:-4]
    cmps.extend(["dat", "miscellaneous"])
    parser.add_argument(
        "-d",
        "--data-path",
        default=os.sep + os.path.join(*cmps),
        help="path containing plate and well FASTQ source",
    )
    parser.add_argument(
        "-s", "--source-scheme", default="file", help="scheme used for the source URL"
    )
    parser.add_argument(
        "-l", "--plate-spec", default="A11967A_sW0154", help="the plate specification"
    )
    parser.add_argument(
        "-w", "--well-spec", default="B01", help="the well specification"
    )
    cmps = str(os.path.abspath(__file__)).split(os.sep)[0:-1]
    parser.add_argument(
        "-c",
        "--config-path",
        default=os.sep + os.path.join(*cmps),
        help="path to a .ini file with args to be passed to the assembler",
    )
    parser.add_argument(
        "-f",
        "--config-file",
        default="Assembler.ini",
        help="path to a .ini file with args to be passed to the assembler",
    )
    parser.add_argument(
        "-o",
        "--output-directory",
        default=None,
        help="the directory containing all output files",
    )
    options = parser.parse_args()
    if options.output_directory is None:
        options.output_directory = options.plate_spec + "_" + options.well_spec
    if not os.path.exists(options.output_directory):
        os.mkdir(options.output_directory)

    # Work within the Toil context manager
    with Toil(options) as toil:
        if not toil.options.restart:

            # Import the local read files into the file store
            read_one_file_ids, read_two_file_ids = utilities.importReadFiles(
                toil,
                options.data_path,
                options.plate_spec,
                [options.well_spec],
                options.source_scheme,
            )

            # Import local config file into the file store
            config_file_id = utilities.importConfigFile(
                toil, os.path.join(options.config_path, options.config_file)
            )

            # Construct and start the Unicycker job
            unicycler_job = UnicyclerJob(
                read_one_file_ids[0],
                read_two_file_ids[0],
                config_file_id,
                options.config_file,
                options.output_directory,
            )
            unicycler_rv = toil.start(unicycler_job)

        else:

            # Restart the Unicycler job
            unicycler_rv = toil.restart(unicycler_job)
            unicycler_rv = toil.restart(unicycler_job)

        # Export all Unicycler output files from the file store
        utilities.exportFiles(
            toil, options.output_directory, unicycler_rv["unicycler_rv"]
        )
