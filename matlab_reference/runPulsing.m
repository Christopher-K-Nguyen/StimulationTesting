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

%% Variables
surfaceArea_arr = File.Parameters.SurfaceArea;
% refElectrode = File.ReferenceElectrode.Type;

% Channels
testChannel_arr = File.Parameters.Channels.Test;
numOfChannels = length(testChannel_arr);

chargePhase_arr = zeros(numOfChannels,1);
chargeInjection_arr = zeros(numOfChannels,1);
dur = File.Test.Duration;
numOfPulses = File.Test.NumberOfPulses;
dur_use = addCommas(dur);
numOfPulses_use = addCommas(numOfPulses);
dur_arr = 0:dur;
periodicPrint = dur / 100;
if rem(periodicPrint,10) ~= 0
    power = ceil(log10(periodicPrint));
    periodicPrint = 10^power;
end
% ratePrint = 1 / periodicPrint;
timestamp_arr = 0:periodicPrint:dur;
timestamp_len = length(timestamp_arr);
% pulsingCheck = rateControl(ratePrint);

% Screen
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

%% Pattern
% Stimulation parameters
expType = File.Test.Experiment;
isLongPulsing = strcmpi(expType,'LP');
if isLongPulsing
    amplitude1_data = File.Parameters.Amplitude1;    % first phase amplitude
    [count,~] = size(amplitude1_data);
    amplitude1_arr = File.Parameters.Amplitude1(count).Amplitude;
    amplitude2_arr = File.Parameters.Amplitude2(count).Amplitude;
else
    amplitude1_arr = File.Parameters.Amplitude1;
    amplitude2_arr = File.Parameters.Amplitude2;
end
amplitude1 = amplitude1_arr(1);
amplitude2 = amplitude2_arr(1);
phaseWidth1 = File.Parameters.PhaseWidth1;        % first phase pulse width
interphaseDelay = File.Parameters.InterphaseDelay;% interphase delay
phaseWidth2 = File.Parameters.PhaseWidth2;        % second phase pulse width
stimRate = File.Parameters.StimulationRate;       % stimulation rate
pattern = struct( ...
    'A1',amplitude1, ...
    'A2',amplitude2, ...
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

% Charge
for channel_idx = 1:numOfChannels
    surfaceArea = surfaceArea_arr(channel_idx);
    [chargePhase,chargeInjection] = getCharge(amplitude1,phaseWidth1,surfaceArea);
    chargePhase_arr(channel_idx) = chargePhase;
    chargeInjection_arr(channel_idx) = chargeInjection;
end

% Set the monitor channel for all stimulators
isQuit = setMonitorChannel(File,1);
if isQuit
    return;
end

% Set trigger
setOscillocopeView(File,amplitude1);
fprintf('\n');

%% Stimulation
fprintf('Starting stimulation for %d s\t(%s pulses)...\n',dur,numOfPulses_use);
variableNames = {...
    'Channel Number',...
    'Current Amplitude (uA)',...
    'Charge/Phase (nC/ph)',...
    'Charge Injection (uC/cm2)'};
stimTable = table(...
    testChannel_arr',...
    amplitude1_arr,chargePhase_arr,chargeInjection_arr,...
    'VariableNames',variableNames);
disp(stimTable);

isDone = false;
totalPulses = 0;
while ~isDone
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

    % Check
    isOver = false;
    % reset(pulsingCheck);
    while ~isOver
        presentTime = toc(startTime);
        progress = presentTime / dur;
        if progress > 1
            progress = 1;
        end
        presentTime_use = addCommas(presentTime);
        presentTime_round_use = addCommas(round(presentTime));
        presentPulses = presentTime * stimRate;
        presentPulses_use = addCommas(presentPulses);
        presentPulses_round_use = addCommas(round(presentPulses));
        msg = sprintf('%s / %s s (%s / %s pulses)', ...
            presentTime_round_use,dur_use, ...
            presentPulses_round_use,numOfPulses_use);
        if presentTime >= dur_arr(time_idx)
            time_idx = time_idx +1;
            waitbar(progress,progBar,msg);
        end
        if presentTime >= dur
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
                fprintf('Time elapsed: %s s\t\t(%s pulses)\n',presentTime_use,presentPulses_use);
            end
        end
    end
    totalPulses = totalPulses + numOfPulses;
    totalPulses_use = addCommas(totalPulses);

    %% Stop stimulation channel
    isQuit = stopStimulation;
    if isQuit
        return;
    end

    %% Complete
    endTime = toc(startTime);
    endTime_use = addCommas(endTime);
    endPulses_fix = endTime * stimRate;
    endPulses_use = addCommas(endPulses_fix);
    fprintf('Pulsing completed: %s s\t(%s pulses)\n',endTime_use,endPulses_use);

    msg = sprintf('Pulsing completed: %s s\t(%s pulses)',endTime_use,endPulses_use);
    quest = sprintf('Do you want to pulse another %s (up to %s)?', ...
        numOfPulses_use,totalPulses_use);
    questMsg = {msg,quest};
    questContinue = questdlg(questMsg,BUTTON_YES,BUTTON_NO,BUTTON_YES);
    switch questContinue
        case BUTTON_YES
            isDone = false;
        case BUTTON_NO
            isDone = true;
        otherwise
            isDone = true;
    end

end

%% Email
emailAddress = File.User.Email;
phone = File.User.Phone;
carrier = File.User.Carrier;
if isLongPulsing
    subjectName = 'Pulsing';
    pulseNum = File.PulsingData(count).PulseNumber;
    pulseNum_new = addCommas(pulseNum);
else
    subject = File.Subject;
    subjectName = sprintf('%s_Pulsing',subject);
end
if ~isempty(emailAddress) || (~isempty(phone) && ~isempty(carrier))
    emailSubject = sprintf('MATLAB Pulsing: %s',subjectName);
    [endTime,unit] = getEndTime(startTime);
    sendMessage(File,emailSubject,[],endTime,unit);
end
fprintf('\n');

end