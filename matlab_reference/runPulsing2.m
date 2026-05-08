function  runPulsing2(File)
close all;
try
    delete(findall(groot));
catch
end
%% Constants
OWNER = 'ckn140030@utdallas.edu';
BLACKROCK_TO_PLEXON_OMNETICS = [9 10 11 12 13 14 15 16 1 2 3 4 5 6 7 8];
NUM_OF_CHANNELS = 16;

% Figure
MSG_WIDTH = 500;
MSG_HEIGHT = 60;
WINDOW_HEADER = 60;

%% Variables
subjectSelect = File.Subject;
surfaceArea = File.SurfaceArea;
% refElectrode = File.ReferenceElectrode.Type;
channelNum_arr = 1:NUM_OF_CHANNELS;
chargePhase_arr = zeros(NUM_OF_CHANNELS,1);
chargeInjection_arr = zeros(NUM_OF_CHANNELS,1);
fields = fieldnames(File);
duration = File.Pulsing.Duration;
totalPulses = File.Pulsing.TotalPulses;
duration_use = addCommas(duration);
totalPulses_use = addCommas(totalPulses);
duration_arr = 0:duration;
periodicPrint = duration / 100;
if rem(periodicPrint,10) ~= 0
    power = ceil(log10(periodicPrint));
    periodicPrint = 10^power;
end
% ratePrint = 1 / periodicPrint;
timestamp_arr = 0:periodicPrint:duration;
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
figPosX = ceil((screenWidth - MSG_WIDTH) / 2) + posX;
figPosY = ceil((screenHeight - MSG_HEIGHT - WINDOW_HEADER) / 2) + posY;

%% Prepare stimulation for each channel
% Stimulation parameters
File.Parameters = File.Parameters;
stimType = File.Parameters.Type;
isLongPulsing = strcmpi(stimType,'LONG');
if isLongPulsing
    amplitude1_data = File.Parameters.Amplitude1;    % first phase amplitude
    [count,~] = size(amplitude1_data);
    amplitude1_arr = File.Parameters.Amplitude1(count).Amplitude;
    amplitude2_arr = File.Parameters.Amplitude2(count).Amplitude;
else
    amplitude1_arr = File.Parameters.Amplitude1;
    amplitude2_arr = File.Parameters.Amplitude2;
end
phaseWidth1 = File.Parameters.PhaseWidth1;        % first phase pulse width
interphaseDelay = File.Parameters.InterphaseDelay;% interphase delay
phaseWidth2 = File.Parameters.PhaseWidth2;        % second phase pulse width
stimRate = File.Pulsing.StimulationRate;       % stimulation rate
numOfPulses = File.Parameters.NumberOfPulses;     % number of pulses
if any(contains(fields,'PercentageOfMax'))
    percentageStim = stimParam.PercentageOfMax; % percentage of max
else
    percentageStim = 100;
end

%% Loop through different currents on each channel
for channelNum = 1:NUM_OF_CHANNELS    % consecutively stimulate channels
    % Channel connection
    if contains(subjectSelect,'C')
        channelStim = BLACKROCK_TO_PLEXON_OMNETICS(channelNum);
    else
        channelStim = channelNum;
    end
    amplitude1 = amplitude1_arr(channelNum);
    amplitude2 = amplitude2_arr(channelNum);
    if amplitude1 == 0
        amplitude1_stim = amplitude1;
        amplitude2_stim = amplitude2;
    else
        fract = percentageStim / 100;
        amplitude1_stim = round(amplitude1 * fract,1);
        amplitude2_stim = round(amplitude2 * fract,1);
    end

    % Set stimulation rate
    quitProgram = setStimRate(1,channelStim,stimRate);
    if quitProgram
        return;
    end

    % Set number of repetitions for all stimulators
    [quitProgram] = setNumOfPulses(1,channelStim,numOfPulses);
    if quitProgram == true
        return;
    end

    % Set rectangular pulse parameters
    pattern.A1 = amplitude1_stim;   	% first phase amplitude
    pattern.A2 = amplitude2_stim; 	% second phase amplitude
    pattern.W1 = phaseWidth1;    	% first phase width
    pattern.W2 = phaseWidth2;     	% second phase width
    pattern.Delay = interphaseDelay;% interphase delay

    % Set stimulation parameters
    quitProgram = setStimParam(1,channelStim,pattern);
    if quitProgram
        return;
    end

    % Load parameters to channel
    [quitProgram] = loadChannel(1,channelStim);
    if quitProgram == true
        return;
    end

    % Charge
    [chargePhase,chargeInjection] = getCharge(amplitude1_stim,phaseWidth1,surfaceArea);
    chargePhase_arr(channelNum) = chargePhase;
    chargeInjection_arr(channelNum) = chargeInjection;
end

% Set the monitor channel for all stimulators
[~,channel_idx] = max(abs(amplitude1_arr));
channel_max = channel_idx(1);
quitProgram = setMonitorChannel(1,channel_max);
if quitProgram
    return;
end

% Set trigger
amplitude1_max = amplitude1_arr(channel_max);
setDefaultScopeView3(File,amplitude1_max);
fprintf('\n');

%% Stimulation
pulses = duration * stimRate;
pulses_use = addCommas(pulses);
fprintf('Starting stimulation for %d s\t(%s pulses)...\n',duration,pulses_use);
variableNames = {...
    'Channel Number',...
    'Current Amplitude (uA)',...
    'Charge/Phase (nC/ph)',...
    'Charge Injection (uC/cm2)'};
stimTable = table(...
    channelNum_arr',...
    amplitude1_arr,chargePhase_arr,chargeInjection_arr,...
    'VariableNames',variableNames);
disp(stimTable);

quitProgram = startStimAllChannel(1);
if quitProgram
    return;
end
startTime = tic;
beep;
progressBar = waitbar(0,'Pulsing','Name','Pulsing Progress',...
    'Position',[figPosX figPosY MSG_WIDTH MSG_HEIGHT]);
progressBar.CloseRequestFcn = '';
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
    progress = presentTime / duration;
    if progress > 1
        progress = 1;
    end
    presentTime_use = addCommas(presentTime);
    presentTime_round_use = addCommas(round(presentTime));
    presentPulses = presentTime * stimRate;
    presentPulses_use = addCommas(presentPulses);
    presentPulses_round_use = addCommas(round(presentPulses));
    msg = sprintf('%s / %s s (%s / %s pulses)',presentTime_round_use,duration_use,presentPulses_round_use,totalPulses_use);
    if presentTime >= duration_arr(time_idx)
        time_idx = time_idx +1;
        waitbar(progress,progressBar,msg);
    end
    if presentTime >= duration
        isOver = true;
        delete(progressBar);
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
        %         timeLeft = duration - presentTime;
        %         if timeLeft < periodicPrint
        %             pause(timeLeft);
        %         else
        %             waitfor(pulsingCheck);
        %         end
    end
end

%% Stop stimulation channel
quitProgram = stopStimAllChannel(1);
if quitProgram
    return;
end
endTime = toc(startTime);
[endTime_use] = getSimplifiedTime(endTime);
fprintf('End stimulation.\n\n');

%% Complete
endPulses_fix = endTime * stimRate;
endPulses_use = addCommas(endPulses_fix);
fprintf('All stimulations completed in %s\t(%s pulses)\n',endTime_use,endPulses_use);

%% Email
emailFiles = {};
emailAddress = File.Email;
if isLongPulsing
    subjectName = 'Pulsing';
    pulseNum = File.PulsingData(count).PulseNumber;
    pulseNum_new = addCommas(pulseNum);
    test = pulseNum_new;
else
    subject = File.Subject;
    test = File.Test;
    subjectName = sprintf('%s_%s Pulsing',subject,test);
end
sendEmail(subjectName,emailAddress,endTime,emailFiles,test);
if ~strcmpi(emailAddress,OWNER)
    sendEmail(subjectName,OWNER,endTime,emailFiles,test);
end
fprintf('\n');

end