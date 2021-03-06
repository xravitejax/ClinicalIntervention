#!user/bin/env python
import os
import sys
import json
import math
import operator
import datetime

from csv import reader
from pyspark import SparkConf, SparkContext

def processtimeseries(chart_events, chart_events_type, icu_static_dict, features_list, hadm_dict=None):
	conf = SparkConf().setAppName("time series")
	sc = SparkContext(conf=conf)

	chart_events_rdd = sc.textFile(chart_events, 1)

	chart_events_rdd = chart_events_rdd.mapPartitions(lambda x: reader(x))

	def isfloat(x):
        	try:
                	float(x)
                	return True
        	except ValueError:
                	return False

	def is_relevant_item(x):
		if chart_events_type=='vitals': itemid = x[4]
		elif chart_events_type=='labs': itemid = x[3]
		for feature in features_list:
			if itemid in feature["ItemIds"]:
				return True
		return False
	chart_events = chart_events_rdd.filter(is_relevant_item)

	if chart_events_type=='labs':
		def is_relevant_hadm(x):
			if x[2] in hadm_dict: return True
			return False 
		chart_events = chart_events.filter(is_relevant_hadm)

        #icu_id --> item_id, value, valuenum, charttime
	if chart_events_type=='vitals':
        	chart_events = chart_events.map(lambda x: (x[3], (x[4], x[8], x[9], x[5])))
	elif chart_events_type=='labs':
		chart_events = chart_events.map(lambda x: (hadm_dict[x[2]] , (x[3], x[5], x[6], x[4])))

	def replace_itemid(x):
		itemid = x[1][0]
		value = x[1][1]
		for feature in features_list:
			if itemid in feature["ItemIds"]:
				if feature["Feature"] == "Temperature":
					if itemid in feature["Fahrenheit"]:
						if isfloat(value):
							value = float(value)
							value = (value-32)*(5.0/9)
					if isfloat(value):
						value = float(value)
						value = math.ceil(value*100)/100
				item = feature["Feature"]
				return (x[0], (item, value, x[1][2], x[1][3]))
		return (x[0], x[1])		
	chart_events = chart_events.map(replace_itemid)

	def is_relevant_icuid(x):
		if x[0] not in icu_static_dict.keys():
			return False
		return True
	chart_events = chart_events.groupByKey().filter(is_relevant_icuid).flatMapValues(lambda x: x)

	def timeseriesmap(x):
		def gettimeindex(starttime, curtime):
			datetimeformat = '%Y-%m-%d %H:%M:%S'
			stime = datetime.datetime.strptime(starttime, datetimeformat)
			try:
				etime = datetime.datetime.strptime(curtime, datetimeformat)
			except:
				return -1
			diff = etime - stime
			return int(diff.total_seconds()/3600)
		
		intime = icu_static_dict[x[0]]['Intime']
		charttime = x[1][3]
		charttimeindex = gettimeindex(intime, charttime)	
		return ((x[0], x[1][0], charttimeindex), x[1][1])
	#icu_id, item_id, charttimeindex --> value
	chart_events = chart_events.map(timeseriesmap)

	def aggregatetimeseries(x):
		return list(x)[0]

	chart_events = chart_events.groupByKey().mapValues(aggregatetimeseries)

	#icu_id, item_id --> value, charttimeindex
	chart_events = chart_events.map(lambda x: ((x[0][0], x[0][1]), [x[1], x[0][2]]))	

	chart_events = chart_events.groupByKey().mapValues(list)	

	def sortlist(x):
		x[1].sort(key=operator.itemgetter(1))
		return (x[0], x[1])

	#icu_id, item_id --> value, charttimeindexsorted
	chart_events = chart_events.map(sortlist)	

	def expandtimeseries(x):
		missing_initial = -2
		missing_middle = -1
		time_series_expand = []
		previous_timeindex = -1
		for value, timeindex in x[1]:
			if timeindex <= previous_timeindex:
				continue
			while(timeindex != previous_timeindex + 1):
				if previous_timeindex == -1:
					time_series_expand.append([missing_initial, previous_timeindex+1])
				else:
					time_series_expand.append([missing_middle, previous_timeindex+1])
				previous_timeindex = previous_timeindex + 1
			previous_timeindex = previous_timeindex + 1
			time_series_expand.append([value,timeindex])
		return (x[0], time_series_expand)

	#icu_id, item_id --> value, charttimeindexsortedwithmissing
	chart_events = chart_events.map(expandtimeseries)

	chart_events.map(lambda x: "{0}\"({1},{2})\" : {3}{4}".format('{', x[0][0], x[0][1], x[1], '}')).saveAsTextFile("icu_timeseries.out")

	#chart_events = chart_events.map(lambda x: (x[0][0], 1)).groupByKey().mapValues(len)
	#chart_events.map(lambda x: "{0},{1}".format(x[0], x[1])).saveAsTextFile("ravi.out")

	return
	
if __name__=='__main__':
	chart_events = sys.argv[1]
	chart_events_type = sys.argv[2] #vitals or labs
	icu_static = sys.argv[3]
	#item_ids = sys.argv[3].strip().split(',')	
	features = sys.argv[4]	

	icu_static_file = open(icu_static, 'r')
	icu_static_json = json.load(icu_static_file)
	icu_static_dict = icu_static_json["icustay_static"]

	features_file = open(features, 'r')
	features_json = json.load(features_file)
	features_list = features_json["Features"]

	hadm_dict = {}
	for icuid, attributes in icu_static_dict.items():
		hadm_dict[attributes['HadmId']] = icuid

	#processtimeseries(chart_events, icu_static_dict, item_ids)
	processtimeseries(chart_events, chart_events_type, icu_static_dict, features_list, hadm_dict)

#spark-submit --conf spark.pyspark.python=/share/apps/python/3.4.4/bin/python sparktimeseries.py /user/rtg267/CHARTEVENTS.csv vitals icu_static.json icu_features.json

#spark-submit --conf spark.pyspark.python=/share/apps/python/3.4.4/bin/python sparktimeseries.py /user/rtg267/LABEVENTS.csv labs icu_static.json icu_features.json
