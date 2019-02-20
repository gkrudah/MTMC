dataset = [];
dataset.numCameras = 8;
dataset.videoParts = [9, 9, 9, 9, 9, 8, 8, 9];

% Set these accordingly
dataset.savePath = 'F:/DukeMTMC/'; % Where to store DukeMTMC (160 GB)

% cleanup dataset.savePath
dataset.savePath = cleanupPath(dataset.savePath);
fprintf(['Download path: ' dataset.savePath '\n']);

GET_ALL               = false; % Set this to true if you want to download everything
GET_GROUND_TRUTH      = true;
GET_CALIBRATION       = true;
GET_VIDEOS            = true;
GET_DPM               = false;
GET_OPENPOSE          = true;
GET_FGMASKS           = false;
GET_REID              = true;
GET_VIDEO_REID        = false;
GET_FRAMES            = false; % Not included in GET_ALL to save space, must be manually set on


%% Create folder structure
fprintf('Creating folder structure...\n');
mkdir(dataset.savePath);
folders = {'ground_truth','calibration','detections','frames','masks','videos', 'detections/DPM', 'detections/openpose'};
for k = 1:length(folders)
    mkdir([dataset.savePath, folders{k}]);
end

for k = 1:dataset.numCameras
    mkdir([dataset.savePath 'frames/camera' num2str(k)]);
    mkdir([dataset.savePath 'masks/camera' num2str(k)]);
    mkdir([dataset.savePath 'videos/camera' num2str(k)]);
end

%% Download ground truth
if GET_ALL || GET_GROUND_TRUTH
    fprintf('Downloading ground truth...\n');
    filenames = {'trainval.mat', 'trainvalRaw.mat'};
    urls = {'http://vision.cs.duke.edu/DukeMTMC/data/ground_truth/trainval.mat', ...
        'http://vision.cs.duke.edu/DukeMTMC/data/ground_truth/trainvalRaw.mat'};
    for k = 1:length(urls)
        url = urls{k};
        filename = sprintf('%sground_truth/%s',dataset.savePath,filenames{k});
        downloadFunc(filename,url)
    end
end
%% Download calibration
if GET_ALL || GET_CALIBRATION
    fprintf('Downloading calibration...\n');
    urls = {'http://vision.cs.duke.edu/DukeMTMC/data/calibration/calibration.txt', ...
        'http://vision.cs.duke.edu/DukeMTMC/data/calibration/camera_position.txt', ...
        'http://vision.cs.duke.edu/DukeMTMC/data/calibration/ROIs.txt'};
    filenames = {'calibration.txt', 'camera_position.txt', 'ROIs.txt'};
    
    for k = 1:length(urls)
        url = urls{k};
        filename = sprintf('%scalibration/%s',dataset.savePath,filenames{k});
        downloadFunc(filename,url)
    end
end

%% Download OpenPose detections
if GET_ALL || GET_OPENPOSE
    for cam = 1:dataset.numCameras
        url = sprintf('http://vision.cs.duke.edu/DukeMTMC/data/detections/openpose/camera%d.mat',cam);
        filename = sprintf('%sdetections/openpose/camera%d.mat',dataset.savePath,cam);
        downloadFunc(filename,url)
    end
end

%% Download videos
if GET_ALL || GET_VIDEOS
    fprintf('Downloading videos (146 GB)...\n');
    for cam = 1:dataset.numCameras
        for part = 0:dataset.videoParts(cam)
            url = sprintf('http://vision.cs.duke.edu/DukeMTMC/data/videos/camera%d/%05d.MTS',cam,part);
            filename = sprintf('%svideos/camera%d/%05d.MTS',dataset.savePath,cam,part);
            downloadFunc(filename,url)
        end
    end
    fprintf('Data download complete.\n');
end

%% Download DPM detections
if GET_ALL || GET_DPM
    fprintf('Downloading detections...\n');
    for cam = 1:dataset.numCameras
        url = sprintf('http://vision.cs.duke.edu/DukeMTMC/data/detections/DPM/camera%d.mat',cam);
        filename = sprintf('%sdetections/DPM/camera%d.mat',dataset.savePath,cam);
        downloadFunc(filename,url)
    end
end

%% Download background masks
if GET_ALL || GET_FGMASKS
    fprintf('Downloading masks...\n');
    for cam = 1:dataset.numCameras
        url = sprintf('http://vision.cs.duke.edu/DukeMTMC/data/masks/camera%d.tar.gz',cam);
        filename = sprintf('%smasks/camera%d.tar.gz',dataset.savePath,cam);
        downloadFunc(filename,url)
    end
    
    % Extract masks
    fprintf('Extracting masks...\n');
    for cam = 1:dataset.numCameras
        filename = sprintf('%smasks/camera%d.tar.gz',dataset.savePath,cam);
        fprintf([filename '\n']);
        untar(filename, [dataset.savePath 'masks']);
    end
    
    % Delete temporary files
    fprintf('Deleting temporary files...\n');
    for cam = 1:dataset.numCameras
        filename = sprintf('%smasks/camera%d.tar.gz',dataset.savePath,cam);
        fprintf([filename '\n']);
        delete(filename);
    end
end

%% Download DukeMTMC-reID
if GET_ALL || GET_REID
    url = 'http://vision.cs.duke.edu/DukeMTMC/data/misc/DukeMTMC-reID.zip';
    filename = sprintf('%s/%s',dataset.savePath,'DukeMTMC-reID.zip');
    downloadFunc(filename,url)
    unzip(filename, dataset.savePath);
end

%% Download DukeMTMC-VideoReID
if GET_ALL || GET_VIDEO_REID
    url = 'http://vision.cs.duke.edu/DukeMTMC/data/misc/DukeMTMC-VideoReID.zip';
    filename = sprintf('%s/%s',dataset.savePath,'DukeMTMC-VideoReID.zip');
    downloadFunc(filename,url)
    unzip(filename, dataset.savePath);
end

%% Extract frames
if GET_FRAMES
    fprintf('Extracting frames...\n');
    ffmpegPath = 'C:/ffmpeg/bin/ffmpeg.exe';
    currDir = pwd;
    for cam = 1:dataset.numCameras
        cd([dataset.savePath 'videos/camera' num2str(cam)]); 
        filelist = '"concat:00000.MTS';
        for k = 1:dataset.videoParts(cam), filelist = [filelist, '|0000', num2str(k), '.MTS']; end; 
        framesDir = [dataset.savePath 'frames/camera' num2str(cam) '/%06d.jpg'];
        command = [ffmpegPath ' -i ' filelist '" -qscale:v 1 -f image2 ' framesDir];
        system(command);
    end
end


%% Functions

function downloadFunc(filename,url)
% Function to download and avoid overriding existing files
    options = weboptions('Timeout', 60);
    
    fprintf([filename '\n']);
    if ~isfile(filename)
        websave(filename,url,options);
    else
        fprintf('Skipped - File exists !!! (delete to redownload)\n');
    end
end

function path = cleanupPath(path)
% cleanup path, ensures that it uses '/' instead of '\' and ends with '/'
    path = strrep(path, '\', '/');
    if ~endsWith(path, '/')
        path = strcat(path, '/');
    end
end
