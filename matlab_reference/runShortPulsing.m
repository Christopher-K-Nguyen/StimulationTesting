function [File,isQuit] = runShortPulsing(File)
close all;
stopStimulation();
try
    delete(findall(groot));
catch
end
%% Constants
% Figure
MSG_WIDTH = 500;
MSG_HEIGHT = 60;
WINDOW_HEADER = 60;

% Buttons
BUTTON_YES = 'Yes';
BUTTON_NO = 'No';

% Camera
QUALITY = 100;

%% Variables
surfaceArea_arr = File.Parameters.SurfaceArea;
numOfSurfaceArea = length(surfaceArea_arr);
% refElectrode = File.ReferenceElectrode.Type;

% Channels
channelGroup_mat = File.Test.Groups;
[numOfGroups,~] = size(channelGroup_mat);
testChannel_arr = channelGroup_mat(:,1);

chargePhase_arr = zeros(numOfGroups,1);
chargeInjection_arr = zeros(numOfGroups,1);
pulsingTime = File.Test.Duration;
numOfPulses = File.Test.NumberOfPulses;
dur_use = addCommas(pulsingTime);
numOfPulses_use = addCommas(numOfPulses);
barUpdate = 0.1;
pulsingTime_arr = 0:barUpdate:pulsingTime;
periodicPrint = pulsingTime / 100;
if rem(periodicPrint,10) ~= 0
    power = ceil(log10(periodicPrint));
    periodicPrint = 10^power;
end
% ratePrint = 1 / periodicPrint;
timestamp_arr = 0:periodicPrint:pulsingTime;
timestamp_len = length(timestamp_arr);
% pulsingCheck = rateControl(ratePrint);

%% Screen
% [screenWidth,screenHeight] = get(0,'Screensize');
screen = get(0,'MonitorPositions');
[numOfScreen,~] = size(screen);
if numOfScreen > 1
    screen1 = screen(1,3);
    screen2 = screen(2,3);
    if screen1 > screen2
        screenWidth = screen(1,3);
        screenHeight = screen(1,4);
        posX = screen(1,1);
        posY = screen(1,2);
    else
        screenWidth = screen(2,3);
        screenHeight = screen(2,4);
        posX = screen(2,1);
        posY = screen(2,2);
    end
else
    screen = get(0,'ScreenSize');
    screenWidth = screen(3);
    screenHeight = screen(4);
    posX = screen(1);
    posY = screen(2);
end
% figPosX = ceil((screenWidth - MSG_WIDTH) / 2) + posX;
% figPosY = ceil((screenHeight - MSG_HEIGHT - WINDOW_HEADER) / 2) + posY;

%% Camera
camState = File.Camera.Enable;
if camState
    % build output filename
    notebook = File.Notebook;
    subject = File.Subject;
    if ~isempty(subject)
        fileName = [notebook '_' subject];
    else
        fileName = notebook;
    end
    File.Name = fileName;
    folderPath = File.Path;
    fileName_vid = [fileName '_vid.mp4'];
    filePath_vid = fullfile(folderPath,fileName_vid);
    File.Camera.Path = filePath_vid;

    % open MPEG-4 writer
    vw = VideoWriter(filePath_vid,'MPEG-4');
    vw.FrameRate = File.Camera.Rate;
    vw.Quality = QUALITY;  % tweak as desired
    open(vw);
    File.Camera.Writer = vw;

    % init in-RAM cache
    File.Video.Time  = zeros(0,1);
    File.Video.Frame = cell(0,1);

    % init next capture time for periodic mode
    mode = File.Camera.Mode;
    if strcmpi(mode,'periodic')
        File.Camera.Next = 0;
    end
end

% Device
deviceType = File.Parameters.Device;

% Channels
plexonChannel_arr = File.Parameters.Channels.Plexon;
% testChannel_arr = File.Parameters.Channels.Test;

%% Pattern
% Stimulation parameters
amplitude1_arr = File.Parameters.Amplitude1;
amplitude2_arr = File.Parameters.Amplitude2;
phaseWidth1 = File.Parameters.PhaseWidth1;        % first phase pulse width
interphaseDelay = File.Parameters.InterphaseDelay;% interphase delay
phaseWidth2 = File.Parameters.PhaseWidth2;        % second phase pulse width
stimRate = File.Parameters.StimulationRate;       % stimulation rate
pattern = struct( ...
    'A1',[], ...
    'A2',[], ...
    'W1',phaseWidth1, ...
    'W2',phaseWidth2, ...
    'Delay',interphaseDelay);

%% Set Parameters
% Set stimulation rate
isQuit = setAllStimRate(stimRate);
if isQuit
    return;
end

% Set number of repetitions for all stimulators
isQuit = setAllNumOfPulses(0);
if isQuit
    return;
end
fprintf('\n');

% Set stimulation parameters
isQuit = setPattern(File,testChannel_arr,pattern);
if isQuit
    return;
end
% Load parameters to channel
isQuit = loadPattern(File);
if isQuit
    return;
end

% Set the monitor channel for all stimulators
isQuit = setMonitorChannel(File,1);
if isQuit
    return;
end

for groupNum = 1:numOfGroups % consecutively stimulate channels
    % Channel connection
    channelNum = channelGroup_mat(groupNum,1);
    % plexonChannel = plexonChannel_arr(channelNum);
    % activeChannelName = sprintf('Channel %02d',channelNum);
    % groupNum = testChannel_arr == channelNum;
    fprintf('\t%s Channel %d...',deviceType,channelNum);
    channelStim = plexonChannel_arr(channelNum);
    fprintf('Plexon Channel %d\n',channelStim);
    File.Data(groupNum).ActiveChannel = channelNum;
    if numOfSurfaceArea > 1
        surfaceArea = surfaceArea_arr(groupNum);
    else
        surfaceArea = surfaceArea_arr;
    end
    File.Data(groupNum).SurfaceArea = surfaceArea;
    amplitude1 = amplitude1_arr(groupNum);
    [chargePhase,chargeInjection] = getCharge(amplitude1,phaseWidth1,surfaceArea);
    chargePhase_arr(groupNum) = chargePhase;
    chargeInjection_arr(groupNum) = chargeInjection;
end

% Set trigger
setOscillocopeView(File,amplitude1);
fprintf('\n');

%% Stimulation
fprintf('Starting stimulation for %d s\t(%s pulses)...\n',pulsingTime,numOfPulses_use);
variableNames = {...
    'Channel Number',...
    'Current Amplitude (uA)',...
    'Charge/Phase (nC/ph)',...
    'Charge Injection (uC/cm2)'};
stimTable = table(...
    testChannel_arr,...
    amplitude1_arr, ...
    chargePhase_arr, ...
    chargeInjection_arr,...
    'VariableNames',variableNames);
disp(stimTable);

%% Start Pulsing
isDone = false;
totalPulses = 0;
while ~isDone
    if camState
        cam = File.Camera.Object;
        himg = preview(cam);
    end
    isQuit = startStimAllChannels(1);
    if isQuit
        return;
    end
    startTime = tic;
    beep;
    progBar = waitbar(0,'Pulsing','Name','Pulsing Progress');
    %'Position',[figPosX figPosY MSG_WIDTH MSG_HEIGHT]
    progBar.CloseRequestFcn = '';
    timestamp_idx = 1;
    time_idx = 1;
    % %% Show Waveform
    % getWaveform(scope,'CH2',1e-3,1,1);
    % getWaveform(scope,'CH1',1,1,1);

    %% Complete Pulsing Duation
    isOver = false;
    % reset(pulsingCheck);
    while ~isOver
        presentTime = toc(startTime);
        progress = presentTime / pulsingTime;
        if progress > 1
            progress = 1;
        end
        presentTime_use = addCommas(presentTime);
        presentTime_round_use = addCommas(round(presentTime));
        presentTime_round_use = append(presentTime_round_use,'.0');
        presentPulses = presentTime * stimRate;
        presentPulses_use = addCommas(presentPulses);
        presentPulses_round_use = addCommas(round(presentPulses));
        msg = sprintf('%s / %s s\n(%s / %s pulses)', ...
            presentTime_round_use,dur_use, ...
            presentPulses_round_use,numOfPulses_use);
        if presentTime >= pulsingTime_arr(time_idx)
            time_idx = time_idx +1;
            waitbar(progress,progBar,msg);
        end
        if presentTime >= pulsingTime
            isOver = true;
            delete(progBar);
        else
            timestamp = timestamp_arr(timestamp_idx);
            if presentTime >= timestamp && timestamp_idx < timestamp_len
                timestamp_idx = timestamp_idx + 1;
                if ~contains(presentTime_use,'.')
                    presentTime_use = sprintf('%s.0000',presentTime_use);
                end
                decimal_idx = strfind(presentPulses_use,'.');
                decimal = presentPulses_use(decimal_idx+1:end);
                decimal_len = length(decimal);
                if ~decimal_len < 4
                    len_diff = 4 - decimal_len;
                    switch len_diff
                        case 1
                            presentPulses_use = sprintf('%s0',presentPulses_use);
                        case 2
                            presentPulses_use = sprintf('%s00',presentPulses_use);
                        case 3
                            presentPulses_use = sprintf('%s000',presentPulses_use);
                    end
                end
                fprintf('Time elapsed: %s s  \t(%s pulses)\n',presentTime_use,presentPulses_use);
            end
        end
        File = getCapture(File, presentTime);
    end
    totalPulses = totalPulses + numOfPulses;
    totalPulses_use = addCommas(totalPulses);

    %% Stop stimulation channel
    isQuit = stopStimulation;
    if isQuit
        return;
    end

    %% Complete
    endTime_s = toc(startTime);
    [endTime,unit] = getEndTime(startTime);
    endTime_use = addCommas(endTime_s);
    endPulses_fix = endTime_s * stimRate;
    endPulses_use = addCommas(endPulses_fix);
    msg = sprintf('Pulsing completed: %s s\t(%s pulses)', ...
        endTime_use,endPulses_use);
    fprintf([msg '\n\n']);
    
    quest = sprintf('Do you want to pulse another %s (up to %s)?', ...
        numOfPulses_use,totalPulses_use);
    questMsg = {msg,quest};
    questContinue = questdlg(questMsg, ...
        'Continuance', ...
        BUTTON_YES,BUTTON_NO, ...
        BUTTON_NO);
    switch questContinue
        case BUTTON_YES
            isDone = false;
        case BUTTON_NO
            isDone = true;
            fprintf('Time elapsed: %.2f %s\n\n',endTime,unit);
        otherwise
            isDone = true;
    end
end

% **close your writer now that you’re done recording**
if camState && isfield(File.Camera,'Writer')
    fprintf('Closing video writer...');
    startTime = tic;
    close(File.Camera.Writer);
    File.Camera.Writer = [];  % clear it so next run re-opens
    [endTime,unit] = getEndTime(startTime);
    fprintf('OK (%.2f %s)\n',endTime,unit);
end

%% Email
emailAddress = File.User.Email;
phone = File.User.Phone;
carrier = File.User.Carrier;
subject = File.Subject;
subjectName = sprintf('%s_Pulsing',subject);
if ~isempty(emailAddress) || (~isempty(phone) && ~isempty(carrier))
    emailSubject = sprintf('MATLAB Pulsing: %s',subjectName);
    [endTime,unit] = getEndTime(startTime);
    sendMessage(File,emailSubject,[],endTime,unit);
end
fprintf('\n');

end