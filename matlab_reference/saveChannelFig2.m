function filepath_tif = saveChannelFig2(...
    File,...
    filename,...
    filepath)
%% File Names
% Channel
channel_arr = [File.Data(:).ActiveChannel];
groupNum = length(channel_arr);
channelNum = channel_arr(groupNum);

% Capture
capture_arr = [File.Data(channelNum).Capture(:).Index];
numOfCaptures = length(capture_arr);
captureNum = capture_arr(numOfCaptures);

% Current
isCurrentMax = File.Data(channelNum).Capture(captureNum).Status.MaxCurrent;

% Data
isGood = File.Data(channelNum).Capture(captureNum).Status.Good;
isBad = ~isGood;

% Starting filename
filename_char = sprintf('%s_CH%02d',filename,channelNum);
filename_tif = sprintf('%s.tif',filename_char);  % filename for .tif file (char)
filepath_tif = fullfile(filepath,filename_tif);
if isfile(filepath_tif)
    delete(filepath_tif);
end
filename_bad = append(filename_char,'_BAD');
filename_bad_tif = sprintf('%s.tif',filename_bad);  % filename for .tif file (char)
filepath_bad_tif = fullfile(filepath,filename_bad_tif);
if isfile(filepath_bad_tif)
    delete(filepath_bad_tif);
end
filename_max = append(filename_char,'_MAX');
filename_max_tif = sprintf('%s.tif',filename_max);  % filename for .tif file (char)
filepath_max_tif = fullfile(filepath,filename_max_tif);
if isfile(filepath_max_tif)
    delete(filepath_max_tif);
end
if isCurrentMax
    filename_tif = filename_max_tif;
    filepath_tif = filepath_max_tif;
elseif isBad
    filename_tif = filename_bad_tif;
    filepath_tif = filepath_bad_tif;
end

% Save figure
try
    vtPlot = File.Data(channelNum).Figure;
    if ishandle(vtPlot)
        fprintf('Saving figure...');
        startTime = tic;
        fprintf('Channel %d...',channelNum);

        print(vtPlot,'-dtiff',filepath_tif,'-r400');
        [endTime,unit] = getEndTime(startTime);
        fprintf('"%s" (%.2f %s)\n',filename_tif,endTime,unit);   % .tif file saved
    else
        filepath_tif = [];
    end
catch
    filepath_tif = [];
end

end