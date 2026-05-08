function [File,confirmCapture] = getCapture_Tek(File)
%% Constants
N_TO_MICRO = 1e6;
MILLI_TO_N = 1e-3;
% Buttons
BUTTON_CONFIRM = 'Confirm';
BUTTON_TRY = 'Try Again';
BUTTON_NEXT = 'Next';
opts.Default = BUTTON_CONFIRM;
opts.Interpreter = 'tex';   % option LaTeX

%% Variables
channelSelect_cell = File.Oscilloscope.Channels;
channelName_cell = File.Oscilloscope.ChannelNames;
numOfChannels = File.Oscilloscope.NumberOfChannels;
capture_arr = [File.Data.Index];
captureNum = length(capture_arr);

%% Function
startCaptureTime = tic;
time = getTime_Tek(File) * N_TO_MICRO;
File.Data(captureNum).Time = time;
for channel_idx = 1:numOfChannels
    channel = channelSelect_cell{channel_idx};
    channelName = channelName_cell{channel_idx};
    [data,~] = getWaveform_Tek(File,channel);
    File.Data(captureNum).(channel) = data;
    if contains2(channelName,'curr')
        currentDensity = getCurrentDensity(data,surfaceArea) * MILLI_TO_N;
        File.Data(captureNum).CurrentDensity = currentDensity;
    end
end
[endCaptureTime,unit] = getEndTime(startCaptureTime);
fprintf('Time Elapse: %.2f %s\n',endCaptureTime,unit);
[File,fig] = getPlot_Tek(File);
File.Data(captureNum).DateTime = getDateTime();
File.Data(captureNum).Figure = fig;

questData = questdlg( ...
    'Do you want to keep and save?', ...
    'Keep Data', ...
    BUTTON_CONFIRM,BUTTON_TRY,BUTTON_NEXT, ...
    opts);
switch questData
    case BUTTON_CONFIRM
        confirmCapture = true;
        isSave = true;
    case BUTTON_TRY
        isSave = false;
    case BUTTON_NEXT
        isSave = false;
end


end