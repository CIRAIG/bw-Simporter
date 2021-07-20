"""
Simporter helps you export your SimaPro project to the brightway2 database hence allowing to open said project in
activity-browser as well.

author: maxime.agez@polymtl.ca
"""

from brightway2 import *
import re
import pkg_resources


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
                 sp_csv_file, db_name, delimiter=';'):
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

        print("Importing files...")

        self.project_name = bw_project_name
        self.ecoinvent_name = ecoinvent_db_name_in_bw
        self.biosphere_name = biosphere_db_name_in_bw
        self.csv_file = sp_csv_file
        self.db_name = db_name
        self.delimiter = delimiter

        file = open(pkg_resources.resource_filename(__name__, '/Data/obsolete_processes.json'), 'r')
        self.obsolete = eval(file.read())

        file = open(pkg_resources.resource_filename(__name__, 'Data/simapro-biosphere_modified_max.json'), 'r')
        self.sp_bio_names = eval(file.read())

        file = open(pkg_resources.resource_filename(__name__, 'Data/list_of_countries.json'), 'r')
        self.countries = eval(file.read())

        self.project_activities = []
        self.sp = ''
        self.obsolete_processes = []
        self.system_processes = []
        self.only_in_simapro = []
        self.created_biosphere_flows = []

        print("Cleaning the csv file...")
        self.cleaning_the_csv_file()

        print("Importing data in brightway2...")
        self.importing_data_to_brightway2()

        print("Connecting to ecoinvent...")
        self.matching_to_ecoinvent()

        print("Connecting to biosphere...")
        self.matching_to_biosphere()

        print("Removing unlinked exchanges...")
        self.removing_unlinked_exchanges()

        print("Writing the database...")
        self.writing_database()

        print("Importing the parameters...")
        self.importing_parameters()

        print("The import was a success. If you have processes if self.obsolete_processes, self.system_processes, "
              "self.only_in_simapro or self.created_biosphere_flows you have to reconnect them manually inside brightway2")

    def cleaning_the_csv_file(self):
        """
        We remove simapro database parameters as they are only useful for the ecoinvent in simapro and create problems
        for the ecoinvent in brightway2
        :return:
        """
        csv = open(self.csv_file)
        txt = csv.read()
        txt_split = txt.split('\n')

        for i, element in enumerate(txt_split):
            if re.findall(r'^[D][a][t][a][b][a][s][e]', element):
                if 'Input' in element:
                    position_database_input_param = i
                elif 'Calculated' in element:
                    position_database_calc_param = i
            if re.findall(r'^[P][r][o][j][e][c][t]', element):
                if 'Input' in element:
                    position_project_input_param = i

        for i in range(position_database_input_param + 1, position_database_calc_param - 2):
            txt_split[i] = txt_split[i].replace(txt_split[i], '')
        for i in range(position_database_calc_param + 1, position_project_input_param - 2):
            txt_split[i] = txt_split[i].replace(txt_split[i], '')

        my_file = open(pkg_resources.resource_filename(__name__, 'Treated_csv_files/'+self.db_name+'.csv'), "w")
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
        self.sp = SimaProCSVImporter(filepath=pkg_resources.resource_filename(__name__, 'Treated_csv_files/'+self.db_name+'.csv'),
                                name=self.db_name,
                                delimiter=self.delimiter)
        self.sp.apply_strategies()
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
                    if self.sp.data[i]['exchanges'][j]['type'] == 'technosphere':
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
                                        'recycling of' in name):
                                    self.only_in_simapro.append(
                                        {'name': self.sp.data[i]['exchanges'][j]['name'], 'origin': self.sp.data[i]['name'],
                                         'amount': self.sp.data[i]['exchanges'][j]['amount']})
                                    continue

                                if (name in ['market for', 'market group for'] or 'to generic market for' in name):
                                    name = name + ' ' + reference_product
                                    try:
                                        ecoinvent_code = [i for i in Database(self.ecoinvent_name).search(
                                                              reference_product, filter={'location': location}) if
                                                          str(i).split("'")[1].split("' ")[0].lower() == name.lower()][0].get('code')
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
                                        ecoinvent_code = [i for i in Database(self.ecoinvent_name).search(
                                                              reference_product, filter={'location': location}) if
                                                          str(i).split("'")[1].split("' ")[0].lower() == name.lower()][0].get('code')
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
                                        ecoinvent_code = [i for i in Database(self.ecoinvent_name).search(
                                                              reference_product, filter={'location': location}) if
                                                          str(i).split("'")[1].split("' ")[0].lower() == name.lower()][0].get('code')
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
                                    ecoinvent_code = [i for i in Database(self.ecoinvent_name).search(
                                                          reference_product, filter={'location': location}) if
                                                      name in i.get('name')][0].get('code')
                                    self.sp.data[i]['exchanges'][j]['output'] = (self.ecoinvent_name, self.sp.data[i]['code'])
                                    self.sp.data[i]['exchanges'][j]['input'] = (self.ecoinvent_name, ecoinvent_code)
                                    continue

                                if name == 'quarry operation':
                                    name = reference_product + ' ' + name
                                    try:
                                        ecoinvent_code = [i for i in Database(self.ecoinvent_name).search(
                                                              reference_product, filter={'location': location}) if
                                                          str(i).split("'")[1].split("' ")[0].lower() == name.lower()][0].get('code')
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
                                        ecoinvent_code = [i for i in Database(self.ecoinvent_name).search(
                                                              reference_product,filter={'location': location}) if
                                                          str(i).split("'")[1].split("' ")[0].lower() == name.lower()][0].get('code')
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
                                    try:
                                        ecoinvent_code = [i for i in Database(self.ecoinvent_name).search(
                                                              reference_product, filter={'location': location})
                                                          if ''.join(i.get('name').split('production')).lower().replace(
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

                                elif re.findall(r'^[p][r][o][d][u][c][t][i][o][n]', name) and name != 'production':
                                    name = reference_product + ' ' + name
                                    try:
                                        ecoinvent_code = [i for i in Database(self.ecoinvent_name).search(
                                                              reference_product, filter={'location': location}) if
                                                          str(i).split("'")[1].split("' ")[0].lower() == name.lower()][0].get('code')
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
                                        ecoinvent_code = [i for i in Database(self.ecoinvent_name).search(
                                                              reference_product, filter={'location': location}) if
                                                          str(i).split("'")[1].split("' ")[0].lower() == name.lower()][0].get('code')
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
        because their name changed at some point. The concordance between the nomenclatures is in the Data folter of
        Simporter.
        :return:
        """
        for i in range(0, len(self.sp.data)):
            for j in range(0, len(self.sp.data[i]['exchanges'])):
                if 'input' not in self.sp.data[i]['exchanges'][j].keys():
                    if self.sp.data[i]['exchanges'][j]['type'] == 'biosphere':

                        name = self.sp.data[i]['exchanges'][j]['name']
                        category = self.sp.data[i]['exchanges'][j]['categories']

                        # rename regionalized flows
                        if name.split(', ')[-1] in self.countries:
                            name = name[:-(len(name.split(', ')[-1]) + 2)]

                        if name in [i[1] for i in self.sp_bio_names]:
                            real_name_in_SP = [_ for _ in self.sp_bio_names if (_[1] == name and _[0] == category[0])][0][2]
                            try:
                                biosphere_code = [_ for _ in Database(self.biosphere_name).search(real_name_in_SP)
                                                  if (_.get('name') == real_name_in_SP and _.get('categories') == category)][0].get('code')
                                self.sp.data[i]['exchanges'][j]['input'] = (self.biosphere_name, biosphere_code)
                                self.sp.data[i]['exchanges'][j]['output'] = (self.ecoinvent_name, self.sp.data[i]['code'])
                            except IndexError:
                                biosphere_code = [_ for _ in Database(self.biosphere_name) if (
                                        _.get('name') == real_name_in_SP and _.get('categories') == category)][0].get('code')
                                self.sp.data[i]['exchanges'][j]['input'] = (self.biosphere_name, biosphere_code)
                                self.sp.data[i]['exchanges'][j]['output'] = (self.ecoinvent_name, self.sp.data[i]['code'])

                        elif name not in [i[1] for i in self.sp_bio_names]:
                            try:
                                biosphere_code = [_ for _ in Database(self.biosphere_name) if (
                                        _.get('name') == name and _.get('categories') == category)][0].get('code')
                                self.sp.data[i]['exchanges'][j]['input'] = (self.biosphere_name, biosphere_code)
                                self.sp.data[i]['exchanges'][j]['output'] = (self.ecoinvent_name, self.sp.data[i]['code'])
                            except IndexError:
                                self.created_biosphere_flows.append({'name': name,
                                                                     'origin': self.sp.data[i]['name'],
                                                                     'amount': self.sp.data[i]['exchanges'][j]['amount']})

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
