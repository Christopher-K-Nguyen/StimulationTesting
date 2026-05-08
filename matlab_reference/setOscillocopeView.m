function File = setOscillocopeView(File,varargin)
%% Constants
TIME_SCALE = [1000 500 250 100 50 25 10 5 2.5];

%% Variables
% Experiment
expType = File.Test.Experiment;
isTriphasic = contains2(expType,'TV');

% Channel
channel_arr = [File.Data(:).ActiveChannel];
groupNum = length(channel_arr);


% Devices
numOfDevices = length(File.Oscilloscope);
numOfTimeScale = length(TIME_SCALE);
if numOfDevices == 1
    fieldsList = vertcat(File.Oscilloscope(:).Fields);
    activeChannel_tf = containsi(fieldsList,{'act','work','pot'});
    diffChannel_tf = containsi(fieldsList,{'diff'});
    voltageChannel_tf = containsi(fieldsList,{'volt'});
    if any(activeChannel_tf)
        specialChannel_idx = find(activeChannel_tf);
    elseif any(diffChannel_tf)
        specialChannel_idx = find(diffChannel_tf);
    elseif any(voltageChannel_tf)
        specialChannel_idx = find(voltageChannel_tf);
    else
        specialChannel_idx = 1;
    end
    scopeChannelSelect_cell = File.Oscilloscope(1).Channels;
    source = scopeChannelSelect_cell{specialChannel_idx};
else
    source = 'CH1';
end

% Configuration
configID = File.Parameters.Configuration.ID;
isMP = contains2(configID,'MP');
isPBP = contains2(configID,'PBP');
isBP = contains2(configID,'BP') && ~isPBP;
isPTP = contains2(configID,'PTP');
isTP = contains2(configID,'TP') && ~isPTP;
isPartial = isPBP || isPTP;
isCG = contains2(configID,'CG');

% Pattern
amplitude1 = File.Parameters.Amplitude1(groupNum);
amplitude2 = File.Parameters.Amplitude2(groupNum);
if isTriphasic
    amplitude3 = File.Parameters.Amplitude3(groupNum);
else
    amplitude3 = [];
end
amplitude_arr = abs([amplitude1 amplitude2 amplitude3]);
[~,amplitude_idx] = sort(amplitude_arr,'descend');
phaseWidth1 = File.Parameters.PhaseWidth1;
phaseWidth2 = File.Parameters.PhaseWidth2;
phaseWidth1_s = phaseWidth1 * 1e-6;
phaseWidth2_s = phaseWidth2 * 1e-6;
if isTriphasic
    phaseWidth3 = File.Parameters.PhaseWidth3;
else
    phaseWidth3 = 0;
end
interphaseDelay = File.Parameters.InterphaseDelay;
interphaseDelay_s = interphaseDelay * 1e-6;
hasInterphaseDelay = interphaseDelay > 0;
dischargeDelay = File.Parameters.DischargeDelay;
hasDischargeDelay = dischargeDelay > 0;
bias = File.Parameters.Bias;
pulseWidth_arr = phaseWidth1 + phaseWidth2 + interphaseDelay;
File.Parameters.PulseWidth = pulseWidth_arr;
if length(pulseWidth_arr) > 1
    pulseWidth = max(pulseWidth_arr);
else
    pulseWidth = pulseWidth_arr;
end
totalPulse = pulseWidth + dischargeDelay;
pulseWidth_s = pulseWidth * 1e-6;
depolTime = File.Parameters.Depolarization;
depolTime_s = depolTime * 1e-6;

% Potential Excursion
if hasInterphaseDelay
    potentialExcursion1_time = phaseWidth1_s + depolTime_s;
    potentialExcursion2_time = pulseWidth_s + depolTime_s;
    if isTriphasic
        phaseWidth3_s = phaseWidth3 * 1e-6;
        potentialExcursion2_time = potentialExcursion2_time - phaseWidth3_s - interphaseDelay_s;
    end
end

% Driving Time
drivingTime_arr = zeros(1,2);
for phase_idx = 1:2
    phaseNum = amplitude_idx(phase_idx);
    switch phaseNum
        case 1
            drivingTime = phaseWidth1_s;
        case 2
            drivingTime = phaseWidth1_s + interphaseDelay_s + phaseWidth2_s;
        case 3
            drivingTime = phaseWidth1_s + phaseWidth2_s + phaseWidth3_s + 2*interphaseDelay_s;
    end
    drivingTime_arr(phase_idx) = drivingTime;
end
drivingTime_arr = sort(drivingTime_arr);

% Input
numOfVar = length(varargin);
if numOfVar > 0
    amplitude_fix = abs(varargin{1});
else
    amplitude_fix = max(amplitude_arr);
end

%% Scope
for deviceNum = 1:numOfDevices
    oscilloscope = File.Oscilloscope(deviceNum).Object;
    model = File.Oscilloscope(deviceNum).Model;
    isTPS = contains2(model,'tps');
    fprintf(oscilloscope,'*CLS');
    %% Channels
    numOfChannels = File.Oscilloscope(deviceNum).NumberOfChannels;
    channelSelect_cell = File.Oscilloscope(deviceNum).Channels;
    hasMathChannel = contains2(channelSelect_cell,'MATH');
    channelName_cell = File.Oscilloscope(deviceNum).ChannelNames;
    current_idx = find(contains(channelName_cell,'curr'));
    voltageChannel_cell = channelSelect_cell;

    if ~hasMathChannel
        % if ~isempty(current_idx)
        %     voltageChannel_cell(current_idx) = [];
        % end
        % source = voltageChannel_cell{1};
    else
        source = 'MATH';
        numOfChannels = numOfChannels + 1;
        voltageChannel_cell{numOfChannels} = 'CH4';
    end

    %% Trigger Delay
    trigSource = File.Oscilloscope(deviceNum).Trigger;
    if contains2(trigSource,'EXT')
        try
            digitalDelay = File.Stimulator.DigitalDelay;
        catch
            digitalDelay = 1;
        end
    else
        digitalDelay = 0;
    end
    digitalDelay_s = digitalDelay * 1e-6;

    %% Horizontal setup
    fprintf('Setting horizontal position...');
    startTime = tic;

    horizScale = 1e-6;
    horizScale_use = sprintf('HORizontal:MAIn:SCAle %.3e',horizScale);
    if isTPS
        horizScale_check = [];
        while ~isequal(horizScale_check,horizScale)
            fprintf(oscilloscope,horizScale_use);
            fprintf(oscilloscope,'HORizontal:MAIN:SCAle?');
            horizScale_raw = fgetl2(oscilloscope);
            horizScale_check = str2double(horizScale_raw);
        end
    else
        fprintf(oscilloscope,horizScale_use);
    end
    for timeScale_idx = 1:numOfTimeScale
        timeScale = TIME_SCALE(timeScale_idx);
        timeWidth = timeScale * 10;
        fract = totalPulse / timeWidth;
        if fract >= 0.3
            break;
        end
    end
    % if fract >= 0.8
    %     shift = 5 * timeScale;
    if fract >= 0.4
        shift = 3 * timeScale;
    % elseif fract >= 0.4
    %     shift = 3 * timeScale;
    else
        shift = 4 * timeScale;
    end
    if contains(trigSource,'EXT')
        %     if timeScale > 10
        %         digitalDelay = round(digitalDelay);
        %     end
        shift_s = (shift + digitalDelay) * 1e-6;
    else
        shift_s = shift * 1e-6;
    end
    % horizPos = round(shift_s,3,'significant');
    horizPos = shift_s;
    horizPos_use = sprintf('HORizontal:MAIN:POSition %.3e',horizPos);
    if isTPS
        horizPos_check = [];
        while ~isequal(horizPos_check,horizPos)
            fprintf(oscilloscope,horizPos_use); % horizontal position
            fprintf(oscilloscope,'HORizontal:MAIN:POSition?');
            horizPos_raw = fgetl2(oscilloscope);
            horizPos_check = str2double(horizPos_raw);
        end
    else
        fprintf(oscilloscope,horizPos_use); % horizontal position
    end
    [endTime,unit] = getEndTime(startTime);
    fprintf('%.3e s \t\t(%.2f %s)\n',horizPos,endTime,unit);

    fprintf('Setting horizontal scale...');
    startTime = tic;
    timeScale_s = timeScale * 1e-6;
    horizScale = round(timeScale_s,3,'significant');
    horizScale_use = sprintf('HORizontal:MAIn:SCAle %.2e',horizScale);
    if isTPS
        horizScale_check = [];
        while ~isequal(horizScale_check,horizScale)
            fprintf(oscilloscope,horizScale_use);
            fprintf(oscilloscope,'HORizontal:MAIN:SCAle?');
            horizScale_raw = fgetl2(oscilloscope);
            horizScale_check = str2double(horizScale_raw);
        end
    else
        fprintf(oscilloscope,horizScale_use);
    end
    File.Oscilloscope(deviceNum).HorizontalScale = timeScale;
    [endTime,unit] = getEndTime(startTime);
    fprintf('%.2e s \t\t(%.2f %s)\n',horizScale,endTime,unit);

    %% Cursor Setup
    fprintf('Opening cursors...');
    startTime = tic;
    source_use = sprintf('CURSor:SELect:SOUrce %s',source);
    fprintf(oscilloscope,source_use);
    fprintf(oscilloscope,'CURSor:FUNCtion VBArs');
    [endTime,unit] = getEndTime(startTime);
    fprintf('OK \t\t(%.2f %s)\n',endTime,unit);
    
    % cursor 1
    fprintf('\tSetting Cursor 1 position...');
    startTime = tic;
    % if hasInterphaseDelay && hasDischargeDelay
    %     cursor1 = potentialExcursion1_time;
    % else
        cursor1 = drivingTime_arr(1);
    % end
    
    cursor1 = cursor1 + digitalDelay_s;
    verticalBar1 = sprintf('CURSor:VBArs:POSITION1 %.2e',cursor1);
    fprintf(oscilloscope,verticalBar1);
    fprintf(oscilloscope,'CURSor:VBArs:POSITION1?');
    cursor1_raw = fgetl2(oscilloscope);
    cursor1_val = str2double(cursor1_raw);
    if ~isequal(cursor1,cursor1_val)
        cursor1 = cursor1 - digitalDelay_s;
        verticalBar1 = sprintf('CURSor:VBArs:POSITION1 %.2e',cursor1);
        fprintf(oscilloscope,verticalBar1);
    end

    [endTime,unit] = getEndTime(startTime);
    fprintf('%.2e s \t\t(%.2f %s)\n',cursor1,endTime,unit);

    % cursor 2
    fprintf('\tSetting Cursor 2 position...');
    startTime = tic;
    % if hasDischargeDelay
    %     cursor2 = potentialExcursion2_time;
    % else
    %     if hasInterphaseDelay
    %         cursor2 = potentialExcursion1_time;
    %     else
    %         if isTriphasic
    %             cursor2 = pulseWidth_s - phaseWidth3_s;
    %         else
                cursor2 = drivingTime_arr(2);
    %         end
    %     end
    % end
    cursor2 = cursor2 + digitalDelay_s;
    verticalBar2 = sprintf('CURSor:VBArs:POSITION2 %.2e',cursor2);
    fprintf(oscilloscope,verticalBar2);
    fprintf(oscilloscope,'CURSor:VBArs:POSITION2?');
    cursor2_raw = fgetl2(oscilloscope);
    cursor2_val = str2double(cursor2_raw);
    if ~isequal(cursor2,cursor2_val)
        cursor2 = cursor2 - digitalDelay_s;
        verticalBar2 = sprintf('CURSor:VBArs:POSITION2 %.2e',cursor2);
        fprintf(oscilloscope,verticalBar2);
    end
    [endTime,unit] = getEndTime(startTime);
    fprintf('%.2e s \t\t(%.2f %s)\n',cursor2,endTime,unit);

    %% Voltage Position and Scale
    if phaseWidth1 >= 100
        if isMP
            vertScale = 1;
        else
            vertScale = 2;
        end
    else
        environment = File.Parameters.Environment;
        if contains2(environment,'A')
            vertScale = 2;
        else
            if isMP
                vertScale = 0.2;
            else
                vertScale = 0.5;
            end
        end
    end
    % configID = File.Parameters.Configuration.ID;
    %     if ~contains2(configID,'MP')
    %         vertScale = vertScale * 4;
    %     end
    for groupNum = 1:numOfChannels
        scopeChannel = voltageChannel_cell{groupNum};
        isMath = contains2(scopeChannel,'MATH');
        fprintf('Setting %s...\n',scopeChannel);
        fprintf('\tSetting position...');
        startTime = tic;
        position = 0;
        if isMath
            position_use = sprintf('%s:VERtical:POSition %.2e',scopeChannel,position);
        else
            position_use = sprintf('%s:POSition %.2e',scopeChannel,position);
        end
        if isTPS
            position_check = [];
            if isMath
                position_query = sprintf('%s:VERtical:POSition?',scopeChannel);
            else
                position_query = sprintf('%s:POSition?',scopeChannel);
            end
            while ~isequal(position_check,position)
                fprintf(oscilloscope,position_use);
                fprintf(oscilloscope,position_query);
                position_raw = fgetl2(oscilloscope);
                position_check = str2double(position_raw);
            end
        else
            fprintf(oscilloscope,position_use);
        end
        [endTime,unit] = getEndTime(startTime);
        fprintf('%.2e V (%.2f %s)\n',position,endTime,unit);

        fprintf('\tSetting scale...');
        startTime = tic;
        if isMath
            vertScale_use = sprintf('%s:VERtical:SCAle %.2e',scopeChannel,vertScale);
        else
            vertScale_use = sprintf('%s:SCAle %.2e',scopeChannel,vertScale);
        end
        if isTPS
            vertScale_check = 0;
            if isMath
                vertScale_query = sprintf('%s:VERtical:SCAle?',scopeChannel);
            else
                vertScale_query = sprintf('%s:SCAle?',scopeChannel);
            end
            checkTime = tic;
            while ~isequal(vertScale_check,vertScale)
                fprintf(oscilloscope,vertScale_use);  % 200 mV/div vertical scale for voltage
                fprintf(oscilloscope,vertScale_query);
                vertScale_raw = fgetl2(oscilloscope);
                vertScale_check = str2double(vertScale_raw);
                if toc(checkTime) > 1
                    break;
                end
            end
        else
            fprintf(oscilloscope,vertScale_use);  % 200 mV/div vertical scale for voltage
        end
        [endTime,unit] = getEndTime(startTime);
        fprintf('%.2e V \t\t(%.2f %s)\n',vertScale,endTime,unit);
    end

    %% Current Scale
    setOscilloscopeCurrentScale(File,amplitude_fix);

    %% Trigger Level
    setTriggerLevelExt(File,deviceNum);

end

end
