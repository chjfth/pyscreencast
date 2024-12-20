#!/usr/bin/env python
# -*- coding: utf-8 -*-

# This is a web server(only for Windows), which cast current screen image to client web browser.
# Client web browser gets an index.html who periodically requests new images from server, via AJAX.

import os, sys, re
import shutil
import filecmp
import time
from datetime import datetime
import thread
from time import struct_time

import win32con
import win32gui
import win32ui 
import win32api
import ctypes
from ctypes import windll
import locale
import ConfigParser
import traceback
# import binascii

import Image
import pyqrcode
import cherrypy

THIS_PY_DIR = os.path.dirname(__file__)
THIS_PROGRAM = os.path.basename(__file__)
sys.path.append( os.path.join(THIS_PY_DIR,'../_pyshare') );
from selfclean_tempfile import selfclean_create_tempfile, tmpfilename_from_epsec

sys_codepage = locale.getpreferredencoding(True)

g_config_ini = os.path.join(THIS_PY_DIR, 'config.ini') # INI content will override following const
g_ini_section = 'global'
SERVER_PORT = 8080 # This is the base-port. Second monitor will be SERVER_PORT+1.
TEMPIMG_PRESERVE_MINUTES = 60 * 10
SCREEN_SAVE_INTERVAL_SECONDS = 2
SCREEN_CROP_LEFT = 0
SCREEN_CROP_RIGHT = 0
SCREEN_CROP_TOP = 0
SCREEN_CROP_BOTTOM = 0
DELETE_TEMP_ON_QUIT = 1 # 1 means yes, 0 means no
MYIP_OVERRIDE = ''
SERVER_SHOW_QRCODE = 1 # 1/0: true/false
DIR_BACKUP_PNG = ""
PNG_BACKUP_PRESERVE_DAYS = 3    # 0 means preserve forever, no cleanup
PNG_BACKUP_SIMULATE_DEL = False # True as debugging purpose, delete_outdated_pngs()
PNG_BACKUP_CHECK_STALE_INTERNAL_SECONDS = 3600
DRAW_MOUSE_CURSOR = True

g_check_stale_png_prev_uesec = 0
g_want_http_server = False

g_quit_flag = 0

class Img:
	def __init__(self, path='', w=0, h=0):
		self.path = path
		self.width = w  # pixel width
		self.height = h # pixel height
		
g_latest_img = None # will be a Img class
g_qr_img = None # will be a Img class

g_testvar = 0

def zero_base_it(idx):
	return idx - 1

def is_dirnamptn_monitor(dirnam, monitor_idxUI):
	m = re.match(r'[0-9]{4}\.[0-9]{2}-monitor%d'%(monitor_idxUI), dirnam)
	return True if m else False

def is_dirnamptn_date(dirnam):
	m = re.match(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', dirnam)
	return True if m else False

class SaveImageError(Exception):
	def __init__(self, errmsg):
		self.errmsg = errmsg
	def __str__(self):
		return self.errmsg

def save_screen_as_bmp(monitr, filepath):
	# Thanks to http://stackoverflow.com/a/3586280/151453

	# monitr is a 3-ele tuple: (hMonitor, hdcMonitor, PyRECT)
	# win32api.EnumDisplayMonitors() returns such tuples.
	# monitr. is like r'\\.\DISPLAY1' or r'\\.\DISPLAY2' etc

	user32 = ctypes.windll.user32
	user32.SetProcessDPIAware()

	moninfo = win32api.GetMonitorInfo(monitr[0])
	screenw = moninfo['Monitor'][2]-moninfo['Monitor'][0]
	screenh = moninfo['Monitor'][3]-moninfo['Monitor'][1]
	winDisplayName = moninfo['Device']
		
	x = SCREEN_CROP_LEFT
	y = SCREEN_CROP_TOP
	w = screenw - SCREEN_CROP_LEFT - SCREEN_CROP_RIGHT
	h = screenh - SCREEN_CROP_TOP - SCREEN_CROP_BOTTOM

	isok = True
	try:
		# hwnd = win32gui.FindWindow(None, "DEV.ahk")
		intDC = win32gui.CreateDC('DISPLAY', winDisplayName, None) # returns an int
			# limitation: mouse cursor and caret will not be captured.
		
		dcWin = win32ui.CreateDCFromHandle(intDC) # dcWin is a PyCDC object (as Src)
		cDC = dcWin.CreateCompatibleDC()        # cDC a also a PyCDC object (as Dst)
		
		dataBitMap = win32ui.CreateBitmap() # returns a PyCBitmap object
		dataBitMap.CreateCompatibleBitmap(dcWin, w, h)
		
		cDC.SelectObject(dataBitMap)
		cDC.BitBlt((0,0),(w, h) , dcWin, (x,y), win32con.SRCCOPY)
			# Note: Win81 entering lock-screen will cause BitBlt to raise win32ui.error .

		if DRAW_MOUSE_CURSOR:
			# Draw current mouse cursor onto the screenshot.

			_flags, hcursor, (curx, cury) = win32gui.GetCursorInfo()
			# -- curx, cury is screen-position from primary monitor
			thisscreen_at_x = moninfo['Monitor'][0]
			thisscreen_at_y = moninfo['Monitor'][1]

			# Note: If an EXE calls ShowCursor(FALSE) to hide the mouse cursor
			# hcursor will be NULL. So we need to check hcursor.

			if hcursor:
				cursorinfo = win32gui.GetIconInfo(hcursor) # returns a PyICONINFO object
				xHotspot = cursorinfo[1]
				yHotspot = cursorinfo[2]

				drawcursor_at_x = curx - thisscreen_at_x - xHotspot
				drawcursor_at_y = cury - thisscreen_at_y - yHotspot

				cDC.DrawIcon((drawcursor_at_x, drawcursor_at_y), hcursor);
		
		dataBitMap.SaveBitmapFile(cDC, filepath)
		
	except win32ui.error as e:
		raise SaveImageError(
			'Got win32ui.error exception in save_screen_as_bmp("%s", "%s").'%(
			winDisplayName, filepath))
	finally:
		# Free Resources
		dcWin.DeleteDC() # No need to do `win32gui.DeleteDC(intDC)` because dcWin represents intDC (hope so)
		cDC.DeleteDC()
		win32gui.DeleteObject(dataBitMap.GetHandle())

	global g_testvar
#	g_testvar+=1; 
#	if g_testvar==3: return 9/(g_testvar-3) # trigger exception (test only)
	

def save_screen_image(monitr, cast_imgpath, tmpdir="", backup_imgpath=None):
	# Capture the screen and save it to a image file.
	# cast_imgpath: the image filepath to save, including dir & filename.
	bmpname = "__temp.bmp"
	if tmpdir or (not cast_imgpath):
		bmppath = os.path.join(tmpdir, bmpname)
	else:
		bmppath = os.path.join(os.path.split(cast_imgpath)[0], bmpname)

	save_screen_as_bmp(monitr, bmppath)
	
	imsrc = Image.open(bmppath)
	
	if cast_imgpath:
		if cast_imgpath.endswith('.jpg'):
			imsrc.save(cast_imgpath, quality=80)
		else:
			imsrc.save(cast_imgpath)

	if backup_imgpath:
		dir_bkimg = os.path.dirname(backup_imgpath)
		if not os.path.exists(dir_bkimg):
			os.makedirs(dir_bkimg)
		imsrc.save(backup_imgpath)

	if cast_imgpath:
		newImg = Img(cast_imgpath, imsrc.size[0], imsrc.size[1])
		return newImg
	else:
		return None


def save_screen_with_timestamp(monitor_idxUI, monitr, imgdir='.', imgextname='.jpg'):
	global g_latest_img # input and output
	global g_check_stale_png_prev_uesec

	# Save current image to a tempimg.
	# Compare the image content of tempimg and g_latest_img.path. 
	# If they are the same, I'll leave g_latest_img.path intact.
	# If they are different, I'll rename tempimg to a timestamp-ed filename 
	# and update g_latest_img.path with this new filename so that 
	# the web server thread will see this updated image path.
	
	if not os.path.exists(imgdir):
		os.makedirs(imgdir)
	
	tmpimgpath = os.path.join(imgdir, '_temp'+imgextname)
	tmpimgdir = os.path.split(tmpimgpath)[0]

	if g_want_http_server:
		# This will create a 0-byte tempfile, so don't call it for non-HTTP-server scene.
		newpath = selfclean_create_tempfile(imgdir, 'screen', imgextname, TEMPIMG_PRESERVE_MINUTES * 60)
	else:
		uesec = time.time()
		newname = tmpfilename_from_epsec(int(uesec), ".%03d"%int(uesec*1000%1000), 'screen', imgextname)
		newpath = os.path.join(imgdir, newname)

	if DIR_BACKUP_PNG:
		now = time.localtime()
		nowyear = time.strftime('%Y', now)

		nowyearmonth = time.strftime('%Y.%m', now) + '-monitor%d'%(monitor_idxUI)
		assert(is_dirnamptn_monitor(nowyearmonth, monitor_idxUI))

		nowdate = time.strftime('%Y-%m-%d', now)
		assert (is_dirnamptn_date(nowdate))

		nowhour = time.strftime('%H', now)

		dir_bkpng = os.path.join(DIR_BACKUP_PNG, nowyearmonth, nowdate, nowhour)
		filename_bkpng = os.path.splitext(os.path.basename(newpath))[0] + '.png'
		filepath_bkpng = os.path.join(dir_bkpng, filename_bkpng)

		uesec_now = int(time.time())
		uesec_diff = uesec_now - g_check_stale_png_prev_uesec
		if uesec_diff > PNG_BACKUP_CHECK_STALE_INTERNAL_SECONDS:
			print("Cleaning up stale backup-pngs...")
			delete_outdated_pngs(monitor_idxUI)
			g_check_stale_png_prev_uesec = uesec_now
	else:
		filepath_bkpng = None
	
	newImg = save_screen_image(monitr, 
		tmpimgpath if g_want_http_server else "", 
		tmpdir=tmpimgdir,
		backup_imgpath=filepath_bkpng)

	if g_want_http_server:
		try:
			if g_latest_img and filecmp.cmp(g_latest_img.path, tmpimgpath):
				return g_latest_img.path # g_latest_img intact
		except OSError:
			# Possibly due to the file referred to by g_latest_img has been deleted by selfclean_create_tempfile().
			# This can happen if:
			#   we sleep our computer for a time period longer than selfclean_create_tempfile()'s 
			#   temp-file preserving period, and then wakeup the computer.
			g_latest_img = None
		
		# Prepare a new g_latest_img.
		
		shutil.move(tmpimgpath, newpath) # the newpath file will be overwritten, yes, the very desired atomic effect
		
		newImg.path = newpath.replace(os.sep, '/')
		g_latest_img = newImg # update g_latest_img
		print "Updated:", g_latest_img.path.replace('/', os.sep) # debug

	if filepath_bkpng:
		print "Backup :", filepath_bkpng
	return

#def generate_castimage_filepath(imgdir):
#	uesec = time.time()
#	newpath = tmpfilename_from_epsec(imgdir, 'screen', imgextname)
	

def is_outdated_by_date(dirnam):
	# dirname should be in format like "2023-12-04".
	# If preserve for 1 day, and now local-date is 2023-12-05 (any hour),
	# I'll consider the preserving duration is still valid.
	# Dirnam "2023-12-04" will be deleted when now local-date is 2023-12-06.

	m = re.match(r'([0-9]{4})-([0-9]{2})-([0-9]{2})', dirnam)
	assert(m)
	ts_old = time.mktime(( int(m.group(1)), int(m.group(2)), int(m.group(3)), 0,0,0, 0,0,0 ))

	today = time.localtime()
	ts_now = time.mktime(( today.tm_year, today.tm_mon, today.tm_mday, 0,0,0, 0,0,0 ))

	ts_diff = int(ts_now) - int(ts_old)
	if ts_diff > PNG_BACKUP_PRESERVE_DAYS*(3600*24):
		return True
	else:
		return False


def delete_outdated_pngs(monitor_idxUI):
	dirpngroot = DIR_BACKUP_PNG
	for root, subdirs, files in os.walk(dirpngroot, topdown=True):

		simu_prefix = "[Simulate]" if PNG_BACKUP_SIMULATE_DEL else ""

		_, rootdirnam = os.path.split(root)
		if is_dirnamptn_monitor(rootdirnam, monitor_idxUI):

			ondisk_subdirs = subdirs[:] # make a copy

			# Check each subdirs, to see if they have been outdated.
			for subdirnam in subdirs[:]:
				if is_dirnamptn_date(subdirnam):

					# If subdirname is already sth like "2023-12-04", no need to recurse into it.
					subdirs.remove(subdirnam)

					# We check/compare the time by dirname itself,
					# not by filesystem recorded modification time.
					if is_outdated_by_date(subdirnam):
						dirpath_remove = os.path.join(root, subdirnam)

						print("%sRmDir stale: %s"%(simu_prefix, dirpath_remove))
						ondisk_subdirs.remove(subdirnam)

						if PNG_BACKUP_SIMULATE_DEL==False:
							try:
								shutil.rmtree(dirpath_remove)
							except OSError as e:
								print("RmDir Error:", e.message)

			# Check if rootdirname itself is empty
			if len(ondisk_subdirs)==0 and len(files)==0:
				print("%sRmDir empty: %s" % (simu_prefix, root))
				if PNG_BACKUP_SIMULATE_DEL==False:
					try:
						os.rmdir(root)
					except OSError as e:
						print("RmDir error:", e.message)

	pass

def nowtimestr_ms_log():
	dtnow = datetime.now()
	timestr = dtnow.strftime('%Y-%m-%d_%H:%M:%S.%f')[:-3] # %f is 6-digit microseconds
	return timestr

def get_tempdir(monitor_idxUI):
	return os.path.abspath( os.path.join(THIS_PY_DIR, '..', 'temp', 'monitor%d'%(monitor_idxUI)) )


def thread_screen_grabber(is_wait_cherrypy, monitor_idxUI, monitr):
	
	# Wait until cherrypy is ready to accept http request. Thanks to: http://stackoverflow.com/q/2988636/151453
	# If cherrypy cannot start(listen port occupied etc), there is no sense to grab the screen 
	# and no sense to show a QR code on server machine's screen.
	if is_wait_cherrypy:
		print "###[worker-thread] cherrypy.server.wait() wait for cherrypy server starting..."
		cherrypy.server.wait()
		print "###[worker-thread] cherrypy.server.wait() done. cherrypy server started.\n"
	
	# Since the server has started, I turn off screen logging.
	cherrypy.log.screen = False
	
	if is_wait_cherrypy:
		gen_QR_html(MYIP_OVERRIDE, SERVER_PORT, monitor_idxUI)

	if DIR_BACKUP_PNG:
		print("DIR_BACKUP_PNG = %s"%(DIR_BACKUP_PNG))
		print("PNG_BACKUP_PRESERVE_DAYS = %d"%(PNG_BACKUP_PRESERVE_DAYS))

	global g_quit_flag
	while g_quit_flag==0:
		
		try:
			save_screen_with_timestamp(monitor_idxUI, monitr, get_tempdir(monitor_idxUI), '.jpg')
		except SaveImageError as e:
			timestr = nowtimestr_ms_log()
			print('#######[%s] %s Will retry later'%(timestr, e.errmsg))
		except:
			timestr = nowtimestr_ms_log()
			print('#######[%s] Got exception in thread_screen_grabber thread. Will retry later.'%(timestr))
			traceback.print_exception(*sys.exc_info()) # print the traceback text.

		inner_sleep_secs = 0.1
		pause_cycles = int(SCREEN_SAVE_INTERVAL_SECONDS/inner_sleep_secs)
		for i in range(pause_cycles):
			time.sleep(inner_sleep_secs)
			if g_quit_flag:
				break

	print "thread_screen_grabber() quitted."
	g_quit_flag = 2



class StringGenerator(object):
	
	def __init__(self, monitor_idx):
		self.monitor_idx = monitor_idx
	
	@cherrypy.expose
	def index(self):
		return open( os.path.join(THIS_PY_DIR, 'index.html') )

	@cherrypy.expose
	def getnewimg_textonly(self): # memo, not used
#		cherrypy.session['mystring'] = some_string # memo 
		return '/temp/'+os.path.split(g_latest_img.path)[1]

	@cherrypy.expose
	@cherrypy.tools.json_out() # this will result in http response header Content-Type: application/json
	def getnewimg(self, _='0'): 
		# note: the param _ is only for workaround of IE11's cache behavior.
		# Ref:
		#	http://stackoverflow.com/questions/25858981/javascript-misbehaving-in-ie-until-dev-tools-opened-not-console-related
		#	http://stackoverflow.com/questions/31107364/weird-ie-javascript-only-works-in-development-mode-f12
		if not g_latest_img:
			return {
				'imgbath' : '/static/whiteblock.png' ,
				'imgtime' : 'server not ready', 
			}
		
		newimg_filename = os.path.split(g_latest_img.path)[1]
		epsec_file = os.path.getmtime(g_latest_img.path)
		imgtime = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(epsec_file))
		return { # return a json object
			'imgbath' : '/temp/'+newimg_filename , # "bath" implies path from web server root
			'imgtime' : imgtime, 
			'imgwidth' : g_latest_img.width,
			'imgheight' : g_latest_img.height,
		}

	@cherrypy.expose
	@cherrypy.tools.json_out() 
	def getqrimg(self): 
		if not g_latest_img:
			return {
			'imgbath' : '/temp/_qrcode_url.png' ,
			'imgtime' : '', 
			'imgwidth' : g_qr_img.width,
			'imgheight' : g_qr_img.height,
		}

	@cherrypy.expose
	@cherrypy.tools.json_out() 
	def set_usertext(self, usertext):
		
		ansi = usertext.encode(sys_codepage, errors='replace')
		print 'Got set_usertext: '+ ansi[:8000] # usertext[:8000]
		
		txtpath = os.path.join(get_tempdir(self.monitor_idx), 'usertext.txt')
		open(txtpath, 'w').write(usertext.encode('utf8')) # If not encode('utf8'), it fails and Chrome gets weird stack trace
		os.system('start "" "%s"'%(txtpath))

	@cherrypy.expose
	@cherrypy.tools.json_out() # this will result in http response header Content-Type: application/json
	def get_usertext(self, _='0'):

		print 'Got get_usertext.'
		txtpath = os.path.join(get_tempdir(self.monitor_idx), 'usertext.txt')
		
		try:
			filetxt = open(txtpath, 'r').read()
		except IOError as e: # may be file not exist
			filetxt = ''
		
		if filetxt:
			txt_utf8 = 'Get text error! Check console log for reason.'
			try:
				txt_utf8 = filetxt.decode('utf8')
			except UnicodeDecodeError as e:
				try:
					txt_utf8 = filetxt.decode(sys_codepage).encode('utf8')
				except:
					traceback.print_exception(*sys.exc_info()) # print the traceback text.
			except:
				traceback.print_exception(*sys.exc_info()) # print the traceback text.
			
		else:
			# create the file:
			txt_utf8 = 'empty now'
			open(txtpath, 'w').write('empty now')
			os.system('start "" "%s"'%(txtpath))

		return { # return a json object
			'usertext' : txt_utf8
		}

#	@cherrypy.expose
#	def display(self):
#		return cherrypy.session['mystring']


# test code: (unused yet)
def gbk_errorPage(**kwargs):
  template = cherrypy._cperror._HTTPErrorTemplate
  return template.encode('gbk').decode('utf8') % kwargs


def start_webserver(monitor_idxUI):
	# Web server code based on tut06.py from http://docs.cherrypy.org/en/latest/tutorials.html
	conf = {
		'/': {
			'tools.sessions.on': False, # True,
			'tools.staticdir.root': os.path.abspath(os.getcwd())
		},
		'/static': {
			'tools.staticdir.on': True,
			'tools.staticdir.dir': os.path.join(THIS_PY_DIR, 'public')
		},
		'/temp': {
			'tools.staticdir.on': True,
			'tools.staticdir.dir': get_tempdir(monitor_idxUI)
		}
	}
	cherrypy.server.socket_port = SERVER_PORT + zero_base_it(monitor_idxUI)
	cherrypy.server.socket_host = '0.0.0.0'
	cherrypy.log.access_file = 'access.log'
	cherrypy.log.error_file = 'error.log'
	
#	cherrypy.error_page.default = gbk_errorPage 
		# AttributeError: 'module' object has no attribute 'error_page'
		# And no luck with http://stackoverflow.com/a/28192448/151453

	
	try:
		cherrypy.quickstart(StringGenerator(monitor_idxUI), '/', conf)
		# print '++++++++++++++++++++++++++'
		# Note: If user press Ctrl+C to quit the server, we'll get here.
		# !!! But, If the server fails to start due to listen port occupied by others, 
		# we will neither get here, nor the following except. It seems that the 
		# error thread kill the whole python process.
	except:
		print '================[CHJ DEBUG] sys.exc_type=%s'%(sys.exc_type)
		traceback.print_exception(*sys.exc_info())


def get_my_ipaddress_str():
	import socket
	hostname = socket.gethostname()
	ipstr = socket.gethostbyname(hostname)
	return ipstr
	

def gen_QR_html(ipstr, http_port_base_, monitor_idxUI):
	# Note: This html(with QR code) is to be viewed on server machine, not on client machine.
	# This QR code will be display on the big meeting room projector screen of the server PC,
	# so that attenders(human) can scan this big QR to reach our web server.
	
	pngdir = get_tempdir(monitor_idxUI) # local FS png path
	pngpath = os.path.join(pngdir, '_qrcode_url.png') # local FS png path
	if not os.path.exists(pngdir):
		os.makedirs(pngdir)

	url_text = 'http://' + ipstr
	http_port = http_port_base_ + zero_base_it(monitor_idxUI)
	if http_port!=80:
		url_text += ':'+str(http_port)
		
	qr = pyqrcode.create(url_text)
	qr.png(pngpath, scale=4, quiet_zone=2)

	# Replace text from html template
	htmlpath = os.path.join(THIS_PY_DIR, '_qrcode_m%d.html'%(monitor_idxUI))
	tmpl_htmlpath = os.path.join(THIS_PY_DIR, 'qrcode.html.template')
	html_text = open(tmpl_htmlpath).read()
	html_text = html_text.replace('http://x.x.x.x', url_text)
	html_text = html_text.replace('${monitor_idxUI}', "%d"%(monitor_idxUI))
	html_text = html_text.replace('${FILEPATH_CONFIG_INI}', g_config_ini)
	open(htmlpath, 'w').write(html_text)

	im_qrcode = Image.open(pngpath)
	g_qr_img = Img(pngpath, im_qrcode.size[0], im_qrcode.size[1])

	print("HTTP client access URL: " + url_text)

	if SERVER_SHOW_QRCODE:
		# Open the system default web browser viewing that html, no blocking myself.
		os.system('start "SomeTitle" "%s"'%(htmlpath)) 
	
	return


def load_ini_configs():
	global SERVER_PORT
	global SCREEN_SAVE_INTERVAL_SECONDS
	global TEMPIMG_PRESERVE_MINUTES
	global SCREEN_CROP_LEFT, SCREEN_CROP_RIGHT, SCREEN_CROP_TOP, SCREEN_CROP_BOTTOM
	global DELETE_TEMP_ON_QUIT
	global MYIP_OVERRIDE
	global SERVER_SHOW_QRCODE
	global DIR_BACKUP_PNG
	global PNG_BACKUP_PRESERVE_DAYS
	global PNG_BACKUP_SIMULATE_DEL
	global DRAW_MOUSE_CURSOR
	
	
	if not os.path.exists(g_config_ini):
		try:
			config_ini_sample = g_config_ini + ".sample"
			print("Generating default %s"%(g_config_ini))
			shutil.copyfile(config_ini_sample, g_config_ini)
		except OSError:
			pass
	
	iniobj = ConfigParser.ConfigParser()
	iniobj.read(g_config_ini)

	try: SERVER_PORT = int(iniobj.get(g_ini_section, 'SERVER_PORT'))
	except: pass
	
	try: 
		TEMPIMG_PRESERVE_MINUTES = int(iniobj.get(g_ini_section, 'TEMPIMG_PRESERVE_MINUTES'))
		if(TEMPIMG_PRESERVE_MINUTES<1):
			TEMPIMG_PRESERVE_MINUTES = 1
	except: 
		pass

	try:
		secs = int(iniobj.get(g_ini_section, 'SCREEN_SAVE_INTERVAL_SECONDS'))
		if(secs<1):
			secs = 1
		SCREEN_SAVE_INTERVAL_SECONDS = secs
	except:
		pass

	try: SCREEN_CROP_LEFT = int(iniobj.get(g_ini_section, 'SCREEN_CROP_LEFT'))
	except: pass

	try: SCREEN_CROP_RIGHT = int(iniobj.get(g_ini_section, 'SCREEN_CROP_RIGHT'))
	except: pass

	try: SCREEN_CROP_TOP = int(iniobj.get(g_ini_section, 'SCREEN_CROP_TOP'))
	except: pass

	try: SCREEN_CROP_BOTTOM = int(iniobj.get(g_ini_section, 'SCREEN_CROP_BOTTOM'))
	except: pass
	
	try: DELETE_TEMP_ON_QUIT = int(iniobj.get(g_ini_section, 'DELETE_TEMP_ON_QUIT'))
	except: pass
	
	try: MYIP_OVERRIDE = iniobj.get(g_ini_section, 'MYIP_OVERRIDE')
	except: pass
	if not MYIP_OVERRIDE:
		MYIP_OVERRIDE = get_my_ipaddress_str()

	try: SERVER_SHOW_QRCODE = int(iniobj.get(g_ini_section, 'SERVER_SHOW_QRCODE'))
	except: pass

	try: DRAW_MOUSE_CURSOR = int(iniobj.get(g_ini_section, 'DRAW_MOUSE_CURSOR'))
	except: pass

	try: 
		DIR_BACKUP_PNG = iniobj.get(g_ini_section, "DIR_BACKUP_PNG")
		
		DIR_BACKUP_PNG = os.path.abspath(DIR_BACKUP_PNG)
		
		if DIR_BACKUP_PNG and not os.path.exists(DIR_BACKUP_PNG):
			os.makedirs(DIR_BACKUP_PNG)
	except OSError:
		print 'Error: Cannot create DIR_BACKUP_PNG folder: "%s".'%(DIR_BACKUP_PNG)
		exit(2)
	except: 
		pass

	try:
		days = int(iniobj.get(g_ini_section, "PNG_BACKUP_PRESERVE_DAYS"))
		if days<0:
			days = 0
		PNG_BACKUP_PRESERVE_DAYS = days

		simudel = int(iniobj.get(g_ini_section, "PNG_BACKUP_SIMULATE_DEL"))
		PNG_BACKUP_SIMULATE_DEL = False if simudel==0 else True
	except:
		pass

def select_a_monitor():
	monitrs = win32api.EnumDisplayMonitors()
	mcount = len(monitrs)
	if(mcount==1):
		return 1, monitrs[0] # Only a single display monitor
	
	is_showpos = False

	# We are going to list monitors to the user.
	"""
	[2023-04-24] There are three idx sequence here.
	* idxENUM : The enumeration index from `monitrs`, starting from 0, continuous.
	* idxDISPLAY : The idx in \\.\DISPLAY1, \\.\DISPLAY2 devicenames
		Note that this sequence may NOT be continuous. For example, from a 3-monitor system,
		If we unplug \\.\DISPLAY2, there will be \\.\DISPLAY1 and \\.DISPLAY3 remaining.
	* idxUI : This is a monitor ordinal displayed to user in Windows control-panel.
		These idx will always be continuous and starts from 1.
	
	I'd like to list multiple monitors in idxENUM order, and, I'll try to match 
	the ordinals to that of idxUI. Such as this:
	
		You have more than one monitors. Please select one to use.
		[1] 2560*1440
		[3] 2160*3840
		[4] 2560*1440
		[5] 1920*1080
		[6] 1920*1200
		[2] 3200*1800 (Primary)
		
		[0] Show position
		Type 1 - 6 and press Enter:0
		You have more than one monitors. Please select one to use.
		[1] \\.\DISPLAY1 , 2560*1440  (0, -1440) - (2560, 0)
		[3] \\.\DISPLAY3 , 2160*3840  (3200, -1066) - (5360, 2774)
		[4] \\.\DISPLAY4 , 2560*1440  (-2560, 360) - (0, 1800)
		[5] \\.\DISPLAY5 , 1920*1080  (-1924, -720) - (-4, 360)
		[6] \\.\DISPLAY6 , 1920*1200  (-1358, 1800) - (562, 3000)
		[2] \\.\DISPLAY2 , 3200*1800  (0, 0) - (3200, 1800)
		
	But, today, I find that "start ms-settings:display" sometimes arranges the ordinals wrongly,
	while colorcpl.exe's [Identify monitors] button does it correctly. My code here matches  
	that of colorcpl.exe, bcz I cannot find a way to match that of ms-settings:display.
	"""
	ar_idxDISPLAY = []
	ar_win32moninfo = []
	for mon in monitrs:
		# mon[0] is a PyHandle to a win32 monitor object, like 0x10001, 0x10003, 0x10005 etc
		# mon[1] is a NULL handle
		# mon[2] is a tuple of (Left, Top, Right, Bottom) coordinates
		win32moninfo = win32api.GetMonitorInfo(mon[0])
		ar_win32moninfo.append(win32moninfo)

		DISPLAYn = win32moninfo['Device']  # contains string like r'\\.\DISPLAY2'.
		DISPLAY_prefix = r'\\.\DISPLAY'
		if (DISPLAYn.startswith(DISPLAY_prefix)):
			idxDISPLAY = int(DISPLAYn[len(DISPLAY_prefix):])
		else:
			idxDISPLAY = 0  # use 0 as invalid value, should not see it

		win32moninfo['idxDISPLAY'] = idxDISPLAY # add our custom key

		ar_idxDISPLAY.append(idxDISPLAY)

	ar_idxDISPLAY.sort()

	mapDISPLAYtoUI = {}
	for i in range(len(ar_idxDISPLAY)):
		mapDISPLAYtoUI[ar_idxDISPLAY[i]] = i + 1

	while True:
		# Let user select a monitor to grab
		print 'You have more than one monitors. Please select one to use.'

		for i, win32moninfo in enumerate(ar_win32moninfo):
			monpos = win32moninfo['Monitor']

			idxDISPLAY = win32moninfo['idxDISPLAY']
			idxUI = mapDISPLAYtoUI[ idxDISPLAY ]
			
			win32moninfo['idxUI'] = idxUI

			screenw = monpos[2]-monpos[0]
			screenh = monpos[3]-monpos[1]
			primary_hint = '(Primary)' if win32moninfo['Flags']==1 else ''
			if not is_showpos:
				print '<%d> %d*%d %s'%(idxUI, screenw, screenh, primary_hint)
			else:
				print(r'<%d> \\.\DISPLAY%d , %d*%d  (%d, %d) - (%d, %d)'%(
					idxUI,
					idxDISPLAY,
					screenw, screenh, # %d*%d resolution
					monpos[0], monpos[1], monpos[2], monpos[3]
					))
		
		if not is_showpos:
			print('')
			print('[0] Show position')
		
		while True:
			ascii_key = raw_input('Type 1 .. %d and press Enter:'%(mcount));
			if len(ascii_key)==1:
				break

		# currently support max 9 monitors
		idxUI = ord(ascii_key)-ord('0')
		if idxUI>=1 and idxUI<=mcount:
			break;
		else:
			is_showpos = True
			continue
	
	# User has selected an idxUI !
	
	""" Note: If user explicitly "Disconnect" a monitor in Win10 `start ms-settings:display`,
	The idxDISPLAY sequences may not be contiguous. For example, no \\.\DISPLAY2 below:
	
You have more than one monitors. Please select one to use.
[3] 3200*1800 (Primary)
[4] 1920*1080
[5] 1920*1200
[1] 2560*1440
[2] 2160*3840

[0] Show position
Type 1 - 5 and press Enter:0
You have more than one monitors. Please select one to use.
[3] \\.\DISPLAY4 , 3200*1800  (0, 0) - (3200, 1800)
[4] \\.\DISPLAY5 , 1920*1080  (-1924, -720) - (-4, 360)
[5] \\.\DISPLAY6 , 1920*1200  (-1358, 1800) - (562, 3000)
[1] \\.\DISPLAY1 , 2560*1440  (-2560, 360) - (0, 1800)
[2] \\.\DISPLAY3 , 2160*3840  (3200, -1066) - (5360, 2774)
Type 1 - 5 and press Enter:5

( We will get idxUI=5, idxENUM=2 .)
	"""
	
	for idxENUM, win32moninfo in enumerate(ar_win32moninfo):
		if(win32moninfo['idxUI']==idxUI):
			break

	return idxUI, monitrs[ idxENUM ]


def IWantPhysicalResolution():
	try:
		# On Win81+, this will succeed:
		windll.shcore.SetProcessDpiAwareness(2) # 2=PROCESS_PER_MONITOR_DPI_AWARE
	except:
		# On Win7, we fall back to this:
		windll.user32.SetProcessDPIAware()


def do_main():
	print "Jimm Chen's %s version 20241215.1"%(THIS_PROGRAM)

	IWantPhysicalResolution()
	
	load_ini_configs()
	
	global g_want_http_server
	g_want_http_server = True if SERVER_PORT>0 else False
		
	
	monitor_idxUI, monitr = select_a_monitor()
	thread.start_new_thread(thread_screen_grabber, (g_want_http_server, monitor_idxUI, monitr))
	
	if g_want_http_server:
		# this does not return until the server finishes, Ctrl+C break, 
		# got python syntax error etc.
		start_webserver(monitor_idxUI) 
	else:
		# a small sleep to avoid two-threads "print" to same line.
		time.sleep(0.1)
		
		print("SERVER_PORT is 0, not acting as HTTP server.")
		while True:
			time.sleep(10)
	
	if DELETE_TEMP_ON_QUIT:
		tempdir = get_tempdir(monitor_idxUI)
		print '\n\n[pyscreencast] deleting temp dir %s ...'%(tempdir)
		shutil.rmtree(tempdir, ignore_errors=True)

	print '\n' + '[pyscreencast] done.'

#	thread.start_new_thread(thread_screen_grabber, (1,))
#
#	time.sleep(10) # let the thread run for 10 seconds
#	g_quit_flag = 1 # tell working thread to quit
#
#	while g_quit_flag==1:
#		time.sleep(0.1)
#
#	print "main thread done."


#	for i in range(1):
#		print "shot %d"%(i)
#		#save_screen_as_bmp("_temp.bmp")
#		save_screen_image("temp.jpg")
#		time.sleep(0.1)

if __name__=='__main__':
	do_main()
