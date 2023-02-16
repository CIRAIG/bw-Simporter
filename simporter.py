"""
Simporter helps you export your SimaPro project to the brightway2 database hence allowing to open said project in
activity-browser as well.

author: maxime.agez@polymtl.ca
"""

from brightway2 import *
from bw2data.parameters import *
import re
import pkg_resources
import logging
import uuid
import copy
import sys
import os


class Simporter:
    """
    Object instance variables:
    --------------------------
            - project_name: name of the bw project
            - ecoinvent_name: name of the ecoinvent database
            - biosphere_name: name of the biosphere database
            - csv_file: path to the csv file from simpaor
            - db_name: name for the created database
            - delimiter: delimiter used in simapro csv export
            - obsolete: list of obsolete processes from simapro
            - project_activities: the name of the processes within the simapro project
            - sp: the object of the SimaProCSVImporter class from brightway2
            - obsolete_processes: the obsolete processes that were used in the simapro project
            - system_processes: the system processes that were used in the simapro project
            - only_in_simapro: the processes created by simapro that were used in the simapro project
            - created_biosphere_flows: potential biosphere flows created by the user in simapro
            - sp_bio_names: the concordance for unlinked elementary flows (based on work from IMPACT World+ team)
            - countries: the list of countries for which spatialized flows are available in simapro

    Object methods:
    --------------
            - cleaning_the_csv_file()
            - importing_data_to_brightway2()
            - matching_to_ecoinvent()
            - matching_to_biosphere()
            - removing_unlinked_exchanges()
            - importing_parameters()
    """
    def __init__(self, bw_project_name, ecoinvent_db_name_in_bw, biosphere_db_name_in_bw,
                 sp_csv_file, db_name, ecoinvent_version_used, delimiter=';'):
        """
        params:
        ------
                bw_project_name: [string] the name of the brightway2 project into which the simapro project will be
                                installed
                ecoinvent_db_name_in_bw: [string] the name of the ecoinvent database with which the simapro project
                                will be linked, e.g., "ecoinvent3.6 cut-off"
                biosphere_db_name_in_bw: [string] the name of the biosphere database with which the simapro project
                                will be linked, by default the name should be "biosphere3"
                sp_csv_file: [string] the path to the simapro csv file containing all the information from the simapro
                                project
                db_name: [string] the name which the importer project of simapro will have
                delimiter: [string] the delimiter which was used in the simapro csv file
        """

        # set up logging tool
        logger = logging.getLogger('bw-simporter')
        logger.setLevel(logging.INFO)
        logger.handlers = []
        formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        ch = logging.StreamHandler()
        ch.setLevel(logging.DEBUG)
        ch.setFormatter(formatter)
        logger.addHandler(ch)
        logger.propagate = False

        logger.info("Importing files...")

        # block print statements from brightway2
        sys.stdout = open(os.devnull, 'w')

        self.project_name = bw_project_name
        self.ecoinvent_name = ecoinvent_db_name_in_bw
        self.biosphere_name = biosphere_db_name_in_bw
        self.csv_file = sp_csv_file
        self.db_name = db_name
        self.ei_version = ecoinvent_version_used
        self.delimiter = delimiter

        with open(pkg_resources.resource_filename(__name__, '/Data/ei'+str(self.ei_version)+'/obsolete_processes.json'),'r') as f:
            self.obsolete = eval(f.read())

        with open(pkg_resources.resource_filename(__name__, 'Data/ei'+str(self.ei_version)+'/simapro-biosphere.json'),'r') as f:
            self.sp_bio_names = eval(f.read())

        with open(pkg_resources.resource_filename(__name__, 'Data/list_of_countries.json'), 'r') as f:
            self.countries = eval(f.read())

        with open(pkg_resources.resource_filename(__name__, 'Data/ei'+str(self.ei_version)+'/comps.json'), 'r') as f:
            self.comps = eval(f.read())

        with open(pkg_resources.resource_filename(__name__, 'Data/ei'+str(self.ei_version)+'/subcomps.json'), 'r') as f:
            self.subcomps = eval(f.read())

        self.project_activities = []
        self.sp = ''
        self.obsolete_processes = []
        self.system_processes = []
        self.only_in_simapro = []
        self.created_biosphere_flows = []
        self.allocation_with_parameters = []

        logger.info("Cleaning the csv file...")
        self.cleaning_the_csv_file()

        logger.info("Importing data in brightway2. This may take a while...")
        self.importing_data_to_brightway2()

        logger.info("Calculating allocations based on parameters...")
        self.dealing_with_allocation_defined_by_parameters()

        logger.info("Conforming SimaPro data to brightway2 data format...")
        self.conform_data_to_brightway_format()

        logger.info("Applying basic brightway2 matching with ecoinvent and biosphere...")
        self.basic_matching_to_ecoinvent_and_biosphere()

        logger.info("Refining the matching with ecoinvent...")
        self.matching_to_ecoinvent()

        logger.info("Refining the matching with biosphere...")
        self.matching_to_biosphere()

        logger.info("Removing unlinked exchanges...")
        self.removing_unlinked_exchanges()

        logger.info("Ensuring parametrized exchanges integrity...")
        self.define_original_amounts()

        logger.info("Writing the database...")
        self.writing_database()

        logger.info("Importing the parameters...")
        self.importing_parameters()

        logger.info("The import was a success. If you have processes in self.obsolete_processes, self.system_processes, "
              "self.only_in_simapro or self.created_biosphere_flows you have to reconnect them manually inside brightway2")

    def cleaning_the_csv_file(self):
        """
        We remove simapro database parameters as they are only useful for the ecoinvent in simapro and create problems
        for the ecoinvent in brightway2
        :return:
        """
        with open(self.csv_file, 'r', encoding="latin-1") as f:
            txt = f.read()
        txt_split = txt.split('\n')

        txt_split = dealing_with_reserved_names(txt_split)

        my_file = open(pkg_resources.resource_filename(__name__, 'Treated_csv_files/'+self.db_name+'.csv'), "w",
                       encoding="latin-1")
        new_file_contents = "\n".join(txt_split)
        my_file.write(str(new_file_contents))
        my_file.close()

    def importing_data_to_brightway2(self):
        """
        We use the SimaProCSVImporter class from brightway2 to import all data into an object called self.sp. Inside
        this object are stored all the information required to recreate the simapro project. We also apply strategies
        from brightway2 to attempt to link as many flows as possible through brightway2 original work. Unfortunately,
        the brightway2 strategies are not enough and we will have to match what has not been matched ourselves.
        :return:
        """
        projects.set_current(self.project_name)
        self.sp = SimaProCSVImporter(filepath=pkg_resources.resource_filename(
            __name__, 'Treated_csv_files/'+self.db_name+'.csv'),
                                     name=self.db_name,
                                     delimiter=self.delimiter)

    def basic_matching_to_ecoinvent_and_biosphere(self):
        self.sp.match_database(self.ecoinvent_name, fields=['name', 'unit', 'reference product', 'location'])
        self.sp.match_database(self.biosphere_name, ignore_categories=True)

    def matching_to_ecoinvent(self):
        """
        After trying to match with brightway2's core functions, we match the rest ourselves through a double for-loop
        (not classy but effective) and a bunch of if statements.
        :return:
        """
        for i in range(0, len(self.sp.data)):
            self.project_activities.append(self.sp.data[i]['name'])

        for i in range(0, len(self.sp.data)):
            for j in range(0, len(self.sp.data[i]['exchanges'])):
                if 'input' not in self.sp.data[i]['exchanges'][j].keys():
                    if self.sp.data[i]['exchanges'][j]['type'] in ['technosphere', 'production']:
                        if self.sp.data[i]['exchanges'][j]['name'] not in self.project_activities:
                            if '|' in self.sp.data[i]['exchanges'][j]['name']:

                                reference_product = self.sp.data[i]['exchanges'][j]['name'].split('| ')[0].split(' {')[0]
                                name = self.sp.data[i]['exchanges'][j]['name'].split('| ')[1].rstrip()
                                location = self.sp.data[i]['exchanges'][j]['name'].split('| ')[0].split(' {')[1].split('}')[
                                    0]

                                if location == 'WECC, US only':
                                    location = 'WECC'

                                if self.sp.data[i]['exchanges'][j]['name'] in self.obsolete:
                                    self.obsolete_processes.append(
                                        {'name': self.sp.data[i]['exchanges'][j]['name'], 'origin': self.sp.data[i]['name'],
                                         'amount': self.sp.data[i]['exchanges'][j]['amount']})
                                    continue

                                if 'Cut-off, S' in self.sp.data[i]['exchanges'][j]['name']:
                                    self.system_processes.append(
                                        {'name': self.sp.data[i]['exchanges'][j]['name'], 'origin': self.sp.data[i]['name'],
                                         'amount': self.sp.data[i]['exchanges'][j]['amount']})
                                    continue

                                if (reference_product == 'Diesel, burned in diesel-electric generating set' or
                                        reference_product == 'Sulfidic tailing, off-site' or
                                        'recycling of' in name):
                                    self.only_in_simapro.append(
                                        {'name': self.sp.data[i]['exchanges'][j]['name'], 'origin': self.sp.data[i]['name'],
                                         'amount': self.sp.data[i]['exchanges'][j]['amount']})
                                    continue

                                if (name in ['market for', 'market group for', 'treatment of'] or
                                        re.findall(r'.*? to generic market for$',name)):
                                    name = name + ' ' + reference_product
                                    try:
                                        ecoinvent_code = [act for act in Database(self.ecoinvent_name).search(
                                                              reference_product, filter={'location': location}) if
                                                          str(act).split("'")[1].split("' ")[0].lower() == name.lower()][0].get('code')
                                        self.sp.data[i]['exchanges'][j]['output'] = (self.ecoinvent_name, self.sp.data[i]['code'])
                                        self.sp.data[i]['exchanges'][j]['input'] = (self.ecoinvent_name, ecoinvent_code)
                                        continue
                                    except IndexError:
                                        ecoinvent_code = [act for act in Database(self.ecoinvent_name) if
                                                          (act.get('name').lower() == name.lower()
                                                           and act.get('reference product').lower() == reference_product.lower()
                                                           and act.get('location') == location)][0].get('code')
                                        self.sp.data[i]['exchanges'][j]['output'] = (self.ecoinvent_name, self.sp.data[i]['code'])
                                        self.sp.data[i]['exchanges'][j]['input'] = (self.ecoinvent_name, ecoinvent_code)
                                        continue

                                if 'treatment of,' in name:
                                    name = name.split(',')[0] + ' ' + reference_product + ',' + name.split(',')[1]
                                    try:
                                        ecoinvent_code = [act for act in Database(self.ecoinvent_name).search(
                                                              reference_product, filter={'location': location}) if
                                                          str(act).split("'")[1].split("' ")[0].lower() == name.lower()][0].get('code')
                                        self.sp.data[i]['exchanges'][j]['output'] = (self.ecoinvent_name, self.sp.data[i]['code'])
                                        self.sp.data[i]['exchanges'][j]['input'] = (self.ecoinvent_name, ecoinvent_code)
                                        continue
                                    except IndexError:
                                        ecoinvent_code = [act for act in Database(self.ecoinvent_name) if
                                                          (act.get('name').lower() == name.lower()
                                                           and act.get('reference product').lower() == reference_product.lower()
                                                           and act.get('location') == location)][0].get('code')
                                        self.sp.data[i]['exchanges'][j]['output'] = (self.ecoinvent_name, self.sp.data[i]['code'])
                                        self.sp.data[i]['exchanges'][j]['input'] = (self.ecoinvent_name, ecoinvent_code)
                                        continue

                                if ('diesel' == name and 'ransport' in reference_product):
                                    name = reference_product + ', ' + name
                                    try:
                                        ecoinvent_code = [act for act in Database(self.ecoinvent_name).search(
                                                              reference_product, filter={'location': location}) if
                                                          str(act).split("'")[1].split("' ")[0].lower() == name.lower()][0].get('code')
                                        self.sp.data[i]['exchanges'][j]['output'] = (self.ecoinvent_name, self.sp.data[i]['code'])
                                        self.sp.data[i]['exchanges'][j]['input'] = (self.ecoinvent_name, ecoinvent_code)
                                        continue
                                    except IndexError:
                                        ecoinvent_code = [act for act in Database(self.ecoinvent_name) if
                                                          (act.get('name').lower() == name.lower()
                                                           and act.get('reference product').lower() == reference_product.lower()
                                                           and act.get('location') == location)][0].get('code')
                                        self.sp.data[i]['exchanges'][j]['output'] = (self.ecoinvent_name, self.sp.data[i]['code'])
                                        self.sp.data[i]['exchanges'][j]['input'] = (self.ecoinvent_name, ecoinvent_code)
                                        continue

                                if name == 'construction':
                                    ecoinvent_code = [act for act in Database(self.ecoinvent_name).search(
                                                          reference_product, filter={'location': location}) if
                                                      name in act.get('name')][0].get('code')
                                    self.sp.data[i]['exchanges'][j]['output'] = (self.ecoinvent_name, self.sp.data[i]['code'])
                                    self.sp.data[i]['exchanges'][j]['input'] = (self.ecoinvent_name, ecoinvent_code)
                                    continue

                                if name == 'quarry operation':
                                    name = reference_product + ' ' + name
                                    try:
                                        ecoinvent_code = [act for act in Database(self.ecoinvent_name).search(
                                                              reference_product, filter={'location': location}) if
                                                          str(act).split("'")[1].split("' ")[0].lower() == name.lower()][0].get('code')
                                        self.sp.data[i]['exchanges'][j]['output'] = (self.ecoinvent_name, self.sp.data[i]['code'])
                                        self.sp.data[i]['exchanges'][j]['input'] = (self.ecoinvent_name, ecoinvent_code)
                                        continue
                                    except IndexError:
                                        ecoinvent_code = [act for act in Database(self.ecoinvent_name) if
                                                          (act.get('name').lower() == name.lower()
                                                           and act.get('reference product').lower() == reference_product.lower()
                                                           and act.get('location') == location)][0].get('code')
                                        self.sp.data[i]['exchanges'][j]['output'] = (self.ecoinvent_name, self.sp.data[i]['code'])
                                        self.sp.data[i]['exchanges'][j]['input'] = (self.ecoinvent_name, ecoinvent_code)
                                        continue

                                if name == 'processing':
                                    name = reference_product
                                    ecoinvent_code = [act for act in Database(self.ecoinvent_name) if
                                                      (act.get('name').lower() == name.lower()
                                                       and act.get('reference product').lower() == reference_product.lower()
                                                       and act.get('location') == location)][0].get('code')
                                    self.sp.data[i]['exchanges'][j]['output'] = (self.ecoinvent_name, self.sp.data[i]['code'])
                                    self.sp.data[i]['exchanges'][j]['input'] = (self.ecoinvent_name, ecoinvent_code)
                                    continue

                                if name == 'gravel and quarry operation':
                                    ecoinvent_code = [act for act in Database(self.ecoinvent_name).search(
                                        reference_product, filter={'location': location}) if
                                                      (str(act).split("'")[1].split("' ")[0].lower() ==
                                                       'gravel and sand quarry operation' and
                                                       act.as_dict()[
                                                           'reference product'] == reference_product.lower())][0].get(
                                        'code')
                                    self.sp.data[i]['exchanges'][j]['output'] = (
                                    self.ecoinvent_name, self.sp.data[i]['code'])
                                    self.sp.data[i]['exchanges'][j]['input'] = (self.ecoinvent_name, ecoinvent_code)
                                    continue

                                if (' in ' in name or ' as ' in name or ' or ' in reference_product or
                                        ' from ' in reference_product):
                                    ecoinvent_code = [act for act in Database(self.ecoinvent_name) if
                                                      (act.get('name').lower() == name.lower()
                                                       and act.get('reference product').lower() == reference_product.lower()
                                                       and act.get('location') == location)][0].get('code')
                                    self.sp.data[i]['exchanges'][j]['output'] = (self.ecoinvent_name, self.sp.data[i]['code'])
                                    self.sp.data[i]['exchanges'][j]['input'] = (self.ecoinvent_name, ecoinvent_code)
                                    continue

                                elif 'production' not in name:
                                    try:
                                        ecoinvent_code = [act for act in Database(self.ecoinvent_name).search(
                                                              reference_product,filter={'location': location}) if
                                                          str(act).split("'")[1].split("' ")[0].lower() == name.lower()][0].get('code')
                                        self.sp.data[i]['exchanges'][j]['output'] = (self.ecoinvent_name, self.sp.data[i]['code'])
                                        self.sp.data[i]['exchanges'][j]['input'] = (self.ecoinvent_name, ecoinvent_code)
                                        continue
                                    except IndexError:
                                        ecoinvent_code = [act for act in Database(self.ecoinvent_name) if
                                                          (act.get('name').lower() == name.lower()
                                                           and act.get('reference product').lower() == reference_product.lower()
                                                           and act.get('location') == location)][0].get('code')
                                        self.sp.data[i]['exchanges'][j]['output'] = (self.ecoinvent_name, self.sp.data[i]['code'])
                                        self.sp.data[i]['exchanges'][j]['input'] = (self.ecoinvent_name, ecoinvent_code)
                                        continue

                                elif 'production' == name:
                                    if len(reference_product.split('production')) == 1:
                                        try:
                                            ecoinvent_code = [act for act in Database(self.ecoinvent_name).search(
                                                                  reference_product, filter={'location': location})
                                                              if ''.join(act.get('name').split('production')).lower().replace(
                                                    ' ', '') == reference_product.lower().replace(' ', '')][0].get('code')
                                            self.sp.data[i]['exchanges'][j]['output'] = (self.ecoinvent_name, self.sp.data[i]['code'])
                                            self.sp.data[i]['exchanges'][j]['input'] = (self.ecoinvent_name, ecoinvent_code)
                                            continue
                                        except IndexError:
                                            ecoinvent_code = [act for act in Database(self.ecoinvent_name) if (
                                                        reference_product.lower().replace(' ', '') == ''.join(
                                                    act.get('name').split('production')).lower().replace(' ', '')
                                                        and name.lower() in act.get('name').lower()
                                                        and act.get('location') == location)][0].get('code')
                                            self.sp.data[i]['exchanges'][j]['output'] = (self.ecoinvent_name, self.sp.data[i]['code'])
                                            self.sp.data[i]['exchanges'][j]['input'] = (self.ecoinvent_name, ecoinvent_code)
                                            continue
                                    elif len(reference_product.split('production')) > 1:
                                        ecoinvent_code = [act for act in Database(self.ecoinvent_name) if (
                                                    act.as_dict()['reference product'].lower() == reference_product.lower() and
                                                    act.get('location') == location and
                                                    ''.join(act.get('name').split('production')).lower().replace(' ','') ==
                                                    ''.join(reference_product.split('production')).lower().replace(' ', ''))][0].get('code')
                                        self.sp.data[i]['exchanges'][j]['output'] = (self.ecoinvent_name, self.sp.data[i]['code'])
                                        self.sp.data[i]['exchanges'][j]['input'] = (self.ecoinvent_name, ecoinvent_code)
                                        continue

                                elif re.findall(r'^[p][r][o][d][u][c][t][i][o][n]', name) and name != 'production':
                                    name = reference_product + ' ' + name
                                    try:
                                        ecoinvent_code = [act for act in Database(self.ecoinvent_name).search(
                                                              reference_product, filter={'location': location}) if
                                                          str(act).split("'")[1].split("' ")[0].lower() == name.lower()][0].get('code')
                                        self.sp.data[i]['exchanges'][j]['output'] = (self.ecoinvent_name, self.sp.data[i]['code'])
                                        self.sp.data[i]['exchanges'][j]['input'] = (self.ecoinvent_name, ecoinvent_code)
                                        continue
                                    except IndexError:
                                        ecoinvent_code = [act for act in Database(self.ecoinvent_name) if
                                                          (act.get('name').lower() == name.lower()
                                                           and act.get('reference product').lower() == reference_product.lower()
                                                           and act.get('location') == location)][0].get('code')
                                        self.sp.data[i]['exchanges'][j]['output'] = (self.ecoinvent_name, self.sp.data[i]['code'])
                                        self.sp.data[i]['exchanges'][j]['input'] = (self.ecoinvent_name, ecoinvent_code)
                                        continue

                                elif 'production' in name:
                                    try:
                                        ecoinvent_code = [act for act in Database(self.ecoinvent_name).search(
                                                              reference_product, filter={'location': location}) if
                                                          str(act).split("'")[1].split("' ")[0].lower() == name.lower()][0].get('code')
                                        self.sp.data[i]['exchanges'][j]['output'] = (self.ecoinvent_name, self.sp.data[i]['code'])
                                        self.sp.data[i]['exchanges'][j]['input'] = (self.ecoinvent_name, ecoinvent_code)
                                        continue
                                    except IndexError:
                                        ecoinvent_code = [act for act in Database(self.ecoinvent_name) if
                                                          (act.get('name').lower() == name.lower()
                                                           and act.get('reference product').lower() == reference_product.lower()
                                                           and act.get('location') == location)][0].get('code')
                                        self.sp.data[i]['exchanges'][j]['output'] = (self.ecoinvent_name, self.sp.data[i]['code'])
                                        self.sp.data[i]['exchanges'][j]['input'] = (self.ecoinvent_name, ecoinvent_code)
                                        continue
                                else:
                                    print(name, reference_product, location, i, j)
                        elif self.sp.data[i]['exchanges'][j]['name'] in self.project_activities:
                            self.sp.data[i]['exchanges'][j]['output'] = (self.ecoinvent_name, self.sp.data[i]['code'])
                            self.sp.data[i]['exchanges'][j]['input'] = (self.sp.db_name, [_ for _ in self.sp.data if
                                                                                _['name'] == self.sp.data[i]['exchanges'][j][
                                                                                    'name']][0]['code'])

    def matching_to_biosphere(self):
        """
        For biosphere flows brightway2 does most of the work, we just need to match the few flows that are unlinked
        because their name changed at some point. The concordance between the nomenclatures is in the Data filter of
        Simporter.
        :return:
        """
        for i in range(0, len(self.sp.data)):
            for j in range(0, len(self.sp.data[i]['exchanges'])):
                if 'input' not in self.sp.data[i]['exchanges'][j].keys():
                    if self.sp.data[i]['exchanges'][j]['type'] == 'biosphere':

                        name = self.sp.data[i]['exchanges'][j]['name']
                        category = self.sp.data[i]['exchanges'][j]['categories']
                        # no regionalization of water flows in brightway, so we fix to the default water flow name
                        if re.findall('^Water, ', name):
                            if category[0] != 'Resources':
                                name = 'Water'
                            else:
                                name = 'Water, unspecified natural origin'

                        # brightway2 does not consider an unspecified subcomp but instead has a one element tuple
                        if category[1] == '':
                            try:
                                biosphere_code = [_ for _ in Database(self.biosphere_name) if (
                                        _.get('name') == name and _.get('categories') ==
                                        (self.comps[category[0]],))][0].get('code')
                            except IndexError:
                                # if you can't find it (IndexError) that's because SimaPro changed the name
                                if name in [i[1] for i in self.sp_bio_names]:
                                    real_name_in_SP = [_ for _ in self.sp_bio_names if (_[1] == name and _[0] ==
                                                                                        self.comps[category[0]])][0][2]
                                    try:
                                        # first try with .search()
                                        biosphere_code = \
                                        [_ for _ in Database(self.biosphere_name).search(real_name_in_SP)
                                         if (_.get('name') == real_name_in_SP and _.get('categories') ==
                                             (self.comps[category[0]],))][0].get('code')
                                    except IndexError:
                                        try:
                                            # if .search() didn't work, try with pure list comprehension
                                            biosphere_code = [_ for _ in Database(self.biosphere_name) if (
                                                    _.get('name') == real_name_in_SP and _.get('categories') ==
                                                    (self.comps[category[0]],))][0].get('code')
                                        except IndexError:
                                            try:
                                                # if it still didn't work try to match only on categories and "fake" name
                                                biosphere_code = \
                                                [_ for _ in Database(self.biosphere_name).search(name) if
                                                 _.get('categories') ==
                                                 (self.comps[category[0]],)][0].get('code')
                                            except IndexError:
                                                # alright this time if it doesnt work just drop in self.created_biosphere_flows
                                                self.created_biosphere_flows.append({'exchange name': name,
                                                                                     'category': category,
                                                                                     'process name': self.sp.data[i][
                                                                                         'name'],
                                                                                     'amount':
                                                                                         self.sp.data[i]['exchanges'][
                                                                                             j]['amount']})
                                # if it's not in our list of modified names of SP then it's created
                                else:
                                    self.created_biosphere_flows.append({'exchange name': name,
                                                                         'category': category,
                                                                         'process name': self.sp.data[i]['name'],
                                                                         'amount': self.sp.data[i]['exchanges'][j][
                                                                             'amount']})
                                    continue
                        elif category[1] != '':
                            try:
                                biosphere_code = [_ for _ in Database(self.biosphere_name) if (
                                        _.get('name') == name and _.get('categories') ==
                                        (self.comps[category[0]], self.subcomps[category[1]]))][0].get('code')
                            except IndexError:
                                # if you can't find it (IndexError) that's because SimaPro changed the name
                                if name in [i[1] for i in self.sp_bio_names]:
                                    real_name_in_SP = [_ for _ in self.sp_bio_names if (_[1] == name and _[0] ==
                                                                                        self.comps[category[0]])][0][2]
                                    try:
                                        # first try with .search()
                                        biosphere_code = \
                                        [_ for _ in Database(self.biosphere_name).search(real_name_in_SP)
                                         if (_.get('name') == real_name_in_SP and _.get('categories') ==
                                             (self.comps[category[0]], self.subcomps[category[1]]))][0].get('code')
                                    except IndexError:
                                        try:
                                            # if .search() didn't work, try with pure list comprehension
                                            biosphere_code = [_ for _ in Database(self.biosphere_name) if (
                                                    _.get('name') == real_name_in_SP and _.get('categories') ==
                                                    (self.comps[category[0]], self.subcomps[category[1]]))][0].get(
                                                'code')
                                        except IndexError:
                                            try:
                                                # if it still didn't work try to match only on categories and "fake" name
                                                biosphere_code = \
                                                [_ for _ in Database(self.biosphere_name).search(name) if
                                                 _.get('categories') ==
                                                 (self.comps[category[0]], self.subcomps[category[1]])][0].get('code')
                                            except IndexError:
                                                # alright this time if it doesnt work just drop in self.created_biosphere_flows
                                                self.created_biosphere_flows.append({'name': name,
                                                                                     'origin': self.sp.data[i]['name'],
                                                                                     'amount':
                                                                                         self.sp.data[i]['exchanges'][
                                                                                             j]['amount']})
                                # if it's not in our list of modified names of SP then it's created
                                else:
                                    self.created_biosphere_flows.append({'exchange name': name,
                                                                         'category': category,
                                                                         'process name': self.sp.data[i]['name'],
                                                                         'amount': self.sp.data[i]['exchanges'][j][
                                                                             'amount']})
                                    continue
                        self.sp.data[i]['exchanges'][j]['input'] = (self.biosphere_name, biosphere_code)
                        self.sp.data[i]['exchanges'][j]['output'] = (self.ecoinvent_name, self.sp.data[i]['code'])

    def removing_unlinked_exchanges(self):
        """
        This method removes all remaining unlinked exchanges. Those are the obsolete & system process, the processes
        created by simapro and the biosphere flows created in simapro. If those exchanges are left empty they prevent
        from writing the database.
        :return:
        """
        # repeat 10 times to ensure everything is gone
        for x in range(0, 10):
            for i in range(0, len(self.sp.data)):
                for j in range(0, len(self.sp.data[i]['exchanges'])):
                    try:
                        if 'input' not in self.sp.data[i]['exchanges'][j].keys():
                            self.sp.data[i]['exchanges'].remove(self.sp.data[i]['exchanges'][j])
                    except IndexError:
                        pass
        # double check that everything is gone
        for i in range(0, len(self.sp.data)):
            for j in range(0, len(self.sp.data[i]['exchanges'])):
                if 'input' not in self.sp.data[i]['exchanges'][j].keys():
                    print("Warning: Issue with exchanges: "+str(i)+', '+str(j))

    def writing_database(self):

        self.sp.write_database()

    def importing_parameters(self):
        """
        Parameters are imported differently and do not go through write_database so we import them now.
        :return:
        """
        # for database parameters
        param_dict = {}
        for i, param in enumerate(self.sp.global_parameters.keys()):
            param_dict[i] = {'name': param}
            for key in self.sp.global_parameters[param]:
                param_dict[i][key] = self.sp.global_parameters[param][key]
        param_list = []
        for key in param_dict:
            param_list.append(param_dict[key])

        self.sp.database_parameters = param_list
        self.sp.write_database_parameters(activate_parameters=True, delete_existing=True)

        # for activity parameters
        for i in range(0, len(self.sp.data)):
            if 'parameters' in self.sp.data[i]:
                all_act_param_data = []

                # add _1 activity parameters to allow bw2 to function properly
                all_act_param_data.append({
                    'name': "_".join((re.sub(r"\s+-", '', re.sub(
                        '\d', '', self.sp.data[i]['name'].replace('-', '_')))).split()) + '_1',
                    'database': self.sp.data[i]['database'],
                    'code': self.sp.data[i]['code'],
                    'amount': 1,
                    'formula': ''
                })

                for param in self.sp.data[i]['parameters']:
                    # format param data as bw2 wants it
                    singular_act_param_data = {
                        'name': param['name'],
                        'database': self.sp.data[i]['database'],
                        'code': self.sp.data[i]['code'],
                        'amount': param['amount']
                    }
                    if 'formula' in param:
                        singular_act_param_data['formula'] = param['formula']
                    else:
                        singular_act_param_data['formula'] = ''

                    all_act_param_data.append(singular_act_param_data)

                group = [j.group for j in ActivityParameter.select() if j.dict['code'] == self.sp.data[i]['code']]
                if group:
                    parameters.new_activity_parameters(all_act_param_data,
                                                       group=group[0],
                                                       overwrite=True)
                else:
                    parameters.new_activity_parameters(all_act_param_data,
                                                       "_".join((re.sub(r"\s+-", '', re.sub(
                                                           '\d', '',
                                                           self.sp.data[i]['name'].replace('-', '_')))).split()) +
                                                       '_' + str(uuid.uuid4().hex),
                                                       overwrite=True)

    def dealing_with_allocation_defined_by_parameters(self):
        """Allocations defined with parameters create a problem as a string is entered as a parameter into bw2
        which only accepts floats. Unfortunately, can't keep the parameter used in the allocation, i.e., can only
        keep the value. That's because brightway2 does not allow the "allocation" key to be defined with a formula."""

        # identify processes with allocations defined with parameters
        self.allocation_with_parameters = []
        for i, process in enumerate(self.sp.data):
            for exc in process["exchanges"]:
                try:
                    if type(exc["allocation"]) == str:
                        if i not in self.allocation_with_parameters:
                            self.allocation_with_parameters.append(i)
                except:
                    pass

        # for these processes we will replace the string value by the float value of the parameter
        for problematic_processes in self.allocation_with_parameters:
            for exc in self.sp.data[problematic_processes]['exchanges']:
                # only for flows with allocation (production exchanges)
                if 'allocation' in exc.keys():
                    # only if allocation is a string
                    if type(exc['allocation']) == str:
                        alloc_name = exc['allocation']
                        try:
                            # if it's an activity parameter which is used to define the allocation
                            if alloc_name.lower() in self.sp.data[problematic_processes]['parameters']:
                                # replace string by float value
                                exc['allocation'] = \
                                self.sp.data[problematic_processes]['parameters'][alloc_name.lower()]['amount']
                            # if it's a global parameter which is used to define the allocation
                            elif alloc_name.lower() in self.sp.global_parameters:
                                # replace string by float value
                                exc['allocation'] = self.sp.global_parameters[alloc_name.lower()]['amount']
                        except KeyError:
                            # if it's a global parameter which is used to define the allocation but no input parameters defined
                            if alloc_name.lower() in self.sp.global_parameters:
                                # replace string by float value
                                exc['allocation'] = self.sp.global_parameters[alloc_name.lower()]['amount']
                            else:
                                raise ValueError("Allocation defined on a parameter that does no exist.")

        # check if there are no more issues
        allocation_with_parameters = []
        for i, process in enumerate(self.sp.data):
            for exc in process["exchanges"]:
                try:
                    if type(exc["allocation"]) == str:
                        if i not in allocation_with_parameters:
                            allocation_with_parameters.append(i)
                except:
                    pass
        assert len(allocation_with_parameters) == 0

    def conform_data_to_brightway_format(self):
        """
        Function does 5 main steps which are generally performed through the .apply-strategies() function of brightway2.
        The latter though, does not deal with multi-output processes correctly. So we redo it.
        The main steps are:
            - changing the format of activity parameters
            - separating each multi-output process in multiple single output processes
            - apply allocation value to the input data of each newly created single output process
            - extract and store the metadata of each process (i.e., name, reference product and amount)
            - create hex codes for each process
        :return:
        """

        # reorganize parameters from a dictionary with names as keys to a list of dictionary
        for i in range(0, len(self.sp.data)):
            # only if parameters are used in the process
            if 'parameters' in self.sp.data[i]:
                params = []
                for process in self.sp.data[i]['parameters']:
                    param_dict = {}
                    param_dict['amount'] = self.sp.data[i]['parameters'][process]['amount']
                    param_dict['comment'] = self.sp.data[i]['parameters'][process]['comment']
                    param_dict['name'] = process
                    if 'uncertainty type' in self.sp.data[i]['parameters'][process]:
                        param_dict['uncertainty type'] = self.sp.data[i]['parameters'][process]['uncertainty type']
                        param_dict['loc'] = self.sp.data[i]['parameters'][process]['loc']
                    if 'formula' in self.sp.data[i]['parameters'][process]:
                        param_dict['formula'] = self.sp.data[i]['parameters'][process]['formula']
                    if 'scale' in self.sp.data[i]['parameters'][process]:
                        param_dict['scale'] = self.sp.data[i]['parameters'][process]['scale']
                    if 'negative' in self.sp.data[i]['parameters'][process]:
                        param_dict['negative'] = self.sp.data[i]['parameters'][process]['negative']
                    params.append(param_dict)
                self.sp.data[i]['parameters'] = params

        # save data on the outputs from multioutput processes (before deleting them from self.sp.data)
        multioutput_products = {}
        for nb in self.allocation_with_parameters:
            multioutput_products[nb] = [i for i in self.sp.data[nb]['exchanges'] if i['type'] == 'production']

        # remove the outputs from the multioutput processes data
        for key in multioutput_products:
            for production_flow in multioutput_products[key]:
                self.sp.data[key]['exchanges'].remove(production_flow)

        # separate each multioutput process into a bunch of single output processes
        for key in multioutput_products:
            for production_flow in multioutput_products[key]:
                new_activity = copy.deepcopy(self.sp.data[key])
                # create metadata
                new_activity['name'] = production_flow['name']
                new_activity['reference product'] = production_flow['name']
                new_activity['production amount'] = production_flow['amount']
                if 'parameters' in self.sp.data[key]:
                    new_activity['parameters'] = self.sp.data[key]['parameters']
                # apply allocation
                for input_data in new_activity['exchanges']:
                    input_data['amount'] *= (production_flow['allocation'] / 100)
                # create the production flow
                prod_exchange = dict.fromkeys(production_flow)
                prod_exchange['name'] = production_flow['name']
                prod_exchange['amount'] = production_flow['amount']
                prod_exchange['type'] = production_flow['type']
                try:
                    prod_exchange['formula'] = production_flow['formula']
                except KeyError:
                    pass
                try:
                    prod_exchange['unit'] = production_flow['unit']
                except KeyyError:
                    pass

                    new_activity['exchanges'].append({prod_exchange})
                # now store new_activity in self.sp.data
                self.sp.data.append(new_activity)

        # need to remove the "empty" multioutput processes that we left behind
        key_removed = 0
        for key in multioutput_products:
            self.sp.data.remove(self.sp.data[key - key_removed])
            key_removed += 1

        # create hex codes for each activity
        for i in self.sp.data:
            i['code'] = str(uuid.uuid4().hex)

        # conform metadata for all processes
        for i in self.sp.data:
            if 'name' not in i:
                production = [j for j in i['exchanges'] if j['type'] == 'production']
                assert len(production) == 1
                production = production[0]
                i['name'] = production['name']
                i['reference product'] = production['name']
                i['production amount'] = production['amount']
                i['unit'] = production['unit']
                if 'formula' in production:
                    i['formula'] = production['formula']
            if not [j for j in i['exchanges'] if j['type'] == 'production']:
                # FORMULAAAAA ????
                i['exchanges'].append({
                    'name': i['name'],
                    'amount': i['production amount'],
                    'type': 'production',
                })

    def define_original_amounts(self):
        for i in self.sp.data:
            if 'exchanges' in i:
                for j in i['exchanges']:
                    if 'formula' in j:
                        j['original_amount'] = j['amount']


def dealing_with_reserved_names(txt_split):
    """
    If the project has the bad habit to use Python-reserved names for its parameters, we have to rename those to be able
    to export to brightway2.
    :return: the csv txt file with modified parameter names
    """

    for i, element in enumerate(txt_split):
        if re.findall(r';iff', element):
            txt_split[i] = txt_split[i].replace(element.split(';')[1], '0')
        if re.findall(r';Iff', element):
            txt_split[i] = txt_split[i].replace(element.split(';')[1], '0')
        if re.findall(r'^Int;', element):
            element = re.sub(r'^Int;', 'switch_int;', element)
            txt_split[i] = element
        if re.findall(r'[*]int;', element):
            element = re.sub(r'[*]int;', '*switch_int;', element)
            txt_split[i] = element
        if re.findall(r'[*]int[/]', element):
            element = re.sub(r'[*]int[/]', '*switch_int/', element)
            txt_split[i] = element
        if re.findall(r'[*]int[*]', element):
            element = re.sub(r'[*]int[*]', '*switch_int*', element)
            txt_split[i] = element
        if re.findall(r'[*]Int', element):
            element = re.sub(r'[*]Int', '*switch_int', element)
            txt_split[i] = element
        if re.findall(r'^as;', element):
            element = re.sub(r'^as;', 'as_;', element)
            txt_split[i] = element
        if re.findall(r'[*]as', element) and not re.findall(r'[*]as_alu', element):
            element = re.sub(r'[*]as', '*as_', element)
            txt_split[i] = element
        if re.findall(r'^AS;', element):
            element = re.sub(r'^AS;', 'as_;', element)
            txt_split[i] = element
        if re.findall(r'[*]AS;', element):
            element = re.sub(r'[*]AS;', '*as_;', element)
            txt_split[i] = element
        if re.findall(r'1[-]as', element) and not re.findall(r'1[-]as_alu', element):
            element = re.sub(r'1[-]as', '1-as_', element)
            txt_split[i] = element
        if re.findall(r'1[-]AS', element) and not re.findall(r'1[-]AS_', element):
            element = re.sub(r'1[-]AS', '1-as_', element)
            txt_split[i] = element
        if re.findall(r'[*]pi;', element):
            element = re.sub(r'[*]pi;', '*3.14;', element)
            txt_split[i] = element
        if re.findall(r'[*]Pi[*]', element):
            element = re.sub(r'[*]Pi[*]', '*3.14*', element)
            txt_split[i] = element
        if re.findall(r'[*]pi[)]', element):
            element = re.sub(r'[*]pi[)]', '*3.14)', element)
            txt_split[i] = element
        if re.findall(r'[*]Pi[)]', element):
            element = re.sub(r'[*]Pi[)]', '*3.14)', element)
            txt_split[i] = element
        if re.findall(r'^add;', element):
            element = re.sub(r'^add;', 'added;', element)
            txt_split[i] = element
        if re.findall(r'add[*]', element):
            element = re.sub(r'add[*]', 'added*', element)
            txt_split[i] = element
        if re.findall(r'^poly;', element):
            element = re.sub(r'^poly;', 'polyy;', element)
            txt_split[i] = element
        if re.findall(r'[+]poly[+]', element):
            element = re.sub(r'[+]poly[+]', '+polyy+', element)
            txt_split[i] = element
        if re.findall(r'^prod;', element):
            element = re.sub(r'^prod;', 'prodd;', element)
            txt_split[i] = element
        if re.findall(r';prod[/]', element):
            element = re.sub(r';prod[/]', ';prodd/', element)
            txt_split[i] = element
        if re.findall(r'empty;', element):
            element = re.sub(r'empty;', 'empty_factor;', element)
            txt_split[i] = element
        if re.findall(r'empty[/]', element):
            element = re.sub(r'empty[/]', 'empty_factor/', element)
            txt_split[i] = element

    return txt_split
