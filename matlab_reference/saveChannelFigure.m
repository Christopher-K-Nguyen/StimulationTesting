function filepath_tif = saveChannelFigure(...
    File,...
    varargin)
%% File Names
fileName = File.Name;
foldPath = File.Path;
dpi = 400;
% Channel
if isempty(varargin)
    channel_arr = [File.Data(:).ActiveChannel];
    groupNum = length(channel_arr);
    figNum = groupNum;
else
    groupNum = varargin{1};
    if length(varargin) > 1
        figNum = varargin{2};
    else
        figNum = groupNum;
    end
end

% Capture
capture_arr = [File.Data(groupNum).Capture(:).Index];
numOfCaptures = length(capture_arr);
captureNum = capture_arr(numOfCaptures);

% Configuration
configID = File.Parameters.Configuration.ID;
isMP = contains2(configID,'MP');
isCG = contains2(configID,'CG');

% Current
isCurrentMax = File.Data(groupNum).Capture(captureNum).Status.MaxCurrent;

% Data
isGood = File.Data(groupNum).Capture(captureNum).Status.Good;
isBad = ~isGood;

% Starting filename
channelName = File.Data(groupNum).Name;
channelID = File.Data(groupNum).ID;
filename_char = [fileName '_' channelID];
filename_tif = [filename_char '.tif'];  % filename for .tif file (char)
filepath_tif = fullfile(foldPath,filename_tif);
if isfile(filepath_tif)
    delete(filepath_tif);
end
filename_bad = [filename_char '_BAD'];
filename_bad_tif = [filename_bad '.tif'];  % filename for .tif file (char)
filepath_bad_tif = fullfile(foldPath,filename_bad_tif);
if isfile(filepath_bad_tif)
    delete(filepath_bad_tif);
end
filename_max = [filename_char '_MAX'];
filename_max_tif = [filename_max '.tif'];  % filename for .tif file (char)
filepath_max_tif = fullfile(foldPath,filename_max_tif);
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
fig = figure(figNum);
if ishandle(fig)
    fprintf('Saving figure...');
    startTime = tic;
    fprintf('%s...',channelName);
    if isfile(filepath_tif)
        delete(filepath_tif);
    end
    while true
        try
            dpi_use = sprintf('-r%d',dpi);
            print(fig,'-dtiff',filepath_tif,dpi_use);
            break;
        catch
        end
    end
    [endTime,unit] = getEndTime(startTime);
    fprintf('"%s" (%.2f %s)\n',filename_tif,endTime,unit);   % .tif file saved
else
    filepath_tif = [];
end

if ~isMP && ~isCG
    fig_arr =  findobj('type','figure');
    numOfFigs = length(fig_arr);
    if numOfFigs > 16
        % fig_arr = h(1:numOfFigs);
        close all;
    end
end

end