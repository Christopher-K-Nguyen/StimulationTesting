function [filepath_mat,filepath_xlsx] = saveData3( ...
    File, ...
    filename, ...
    filepath)
%% Variables
fprintf('Setting up files...');
startTime = tic;
stimType = File.Test.ID;
isMultiTest = contains(stimType,'MULTI');
external = File.Parameters.External;
hasExternal = ~isempty(external);
dataLength = File.Oscilloscope(1).Settings.DataLength;

% Channels
channel_arr = [File.Data(:).Channel];
numOfChannels = length(channel_arr);
channelNum = channel_arr(numOfChannels);

% Captures
capture_arr = [File.Data(channelNum).Capture(:).Index];
numOfCaptures = length(capture_arr);
captureNum = capture_arr(numOfCaptures);

% Initialize Arrays
data_arr = zeros(dataLength,1);

time = data_arr;      % time data array
voltage = data_arr;   % voltage data array
if isMultiTest
    working = data_arr;   % voltage data arr
    counter = data_arr;   % voltage data array
end
current = data_arr;   % current data array
currentDensity = data_arr;   % current density data array
amplitude = 0;      % amplitude value
isAtMaxCurrent = false;
chargePhase = 0;    % charge per phase value
surfaceArea = 0;    % geometric surface area
chargeInjection = 0;% charge injection value
dateTime = '';      % date and time completed
status = '';        % status
isGood = true;
potentialExcursion_label = '';

zero_arr = zeros(1,numOfChannels);
channelName_cell = cell(1,numOfChannels);
amplitude_arr = zero_arr;
chargePhase_arr = zero_arr;
chargeInjection_arr = zero_arr;
potentialExcursion_arr = zero_arr;
accessVoltage_arr = zero_arr;
drivingVoltage_arr = zero_arr;
accessResistance_arr = zero_arr;

%% File Names
% .mat filename
filename_mat = append(filename,'.mat');       	% filename for .mat file
filepath_mat = fullfile(filepath,filename_mat);  % save path for .mat file

% .xlsx filename
filename_xlsx = append(filename,'.xlsx');           % filename for .xlsx file
filepath_xlsx = fullfile(filepath,filename_xlsx); 	% save path for .xlsx file
if isfile(filepath_xlsx) && channelNum == 1
   delete(filepath_xlsx);
end
writetable(table,filepath_xlsx,'Sheet','Values');
[endTime,unit] = getEndTime(startTime);
fprintf('"%s" (%.2f %s)\n',filename,endTime,unit);

%% Close Files
fclose('all');
File = setScopeStatus(File,'close');
File.Oscilloscope.Object = [];

%% Save
% Making .mat file
fprintf('Saving MATLAB file...');
startTime = tic;
if numOfChannels == 16
    File.Oscilloscope.Object = [];
end
varName = getVarName(File);
newVarName = ['File_' filename];
setNewVarName = sprintf('%s = %s;',newVarName,varName);
eval(setNewVarName)
save(filepath_mat,newVarName);             	% save .mat file
[endTime,unit] = getEndTime(startTime);
fprintf('"%s" (%.2f %s)\n',filename_mat,endTime,unit);         	% .mat file saved

% Making spreadsheet files
fprintf('Saving Excel file...');
startTime = tic;
for channel_idx = 1:numOfChannels % loop through channel data
    % Capture data of channel
    capture_arr = [File.Data(channel_idx).Capture.Index];
    captureNum = length(capture_arr);
    time = File.Data(channel_idx).Capture(captureNum).Time;              % time data array
    voltage = File.Data(channel_idx).Capture(captureNum).Voltage;            % voltage data array
    if isMultiTest
        working = File.Data(channel_idx).Capture(captureNum).Working;
        if hasExternal
            counter = File.Data(channel_idx).Capture(captureNum).Counter;
        end
    end
    current = File.Data(channel_idx).Capture(captureNum).Current;          % current data array
    currentDensity = File.Data(channel_idx).Capture(captureNum).CurrentDensity;
    amplitude = File.Data(channel_idx).Capture(captureNum).Amplitude;      	% amplitude value
    chargePhase = File.Data(channel_idx).Capture(captureNum).ChargePhase;      	% charge per phase value
    surfaceArea = File.Data(channel_idx).SurfaceArea;                   % geometric surface area
    chargeInjection = File.Data(channel_idx).Capture(captureNum).ChargeInjection;           % charge injection value
    potentialExcursion = File.Data(channel_idx).Capture(captureNum).PotentialExcursion;   % maximum cathodal potential value
    accessVoltage = File.Data(channel_idx).Capture(captureNum).AccessVoltage;
    drivingVoltage = File.Data(channel_idx).Capture(captureNum).DrivingVoltage;           % driving voltage value
    accessResistance = File.Data(channel_idx).Capture(captureNum).AccessResistance;
    dateTime = File.Data(channel_idx).Capture(captureNum).DateTime;                   % date and time completed
    status = File.Data(channel_idx).Capture(captureNum).Status.Description;
    try
        isAtMaxCurrent = File.Data(channel_idx).Capture(captureNum).Status.MaxCurrent;
    catch
        isAtMaxCurrent = false;
    end
    isGood = File.Data(channel_idx).Capture(captureNum).Status.Good;

    % Initialize Arrays
    data_len = length(time);
    cell_arr = cell(data_len,1);	% array of blank space
    amplitude_cell = cell_arr;
    chargePhase_cell = cell_arr;
    surfaceArea_cell = cell_arr;
    chargeInjection_cell = cell_arr;
    potentialExcursion_cell = cell_arr;
    accessVoltage_cell = cell_arr;
    drivingVoltage_cell = cell_arr;
    accessResistance_cell = cell_arr;
    dateTime_cell = cell_arr;
    status_cell = cell_arr;

    % Single Values
    amplitude_cell{1} = amplitude;
    chargePhase_cell{1} = chargePhase;
    surfaceArea_cell{1} = surfaceArea;
    chargeInjection_cell{1} = chargeInjection;
    dateTime_cell{1} = dateTime;
    status_cell{1} = status;

    % Multiple Values
    numOfValues = length(potentialExcursion);
    for idx = 1:numOfValues
        potentialExcursion_cell{idx} = potentialExcursion(idx);
        accessVoltage_cell{idx} = accessVoltage(idx);
        drivingVoltage_cell{idx} = drivingVoltage(idx);
        accessResistance_cell{idx} = accessResistance(idx);
    end
    % Potential Excursion Unit
    if amplitude <= 0
        potentialExcursion_label = 'Emc (V)';
    else
        potentialExcursion_label = 'Ema (V)';
    end

    % Compile
    if isGood
        channelSheet = sprintf('%d',channel_idx);     % channel number (char)
        channelName = channelSheet;
    elseif isAtMaxCurrent
        channelSheet = sprintf('%d_MAX',channel_idx);     % channel number (char)
        channelName = sprintf('%d (MAX)',channel_idx);     % channel number (char)
    else
        channelSheet = sprintf('%d_BAD',channel_idx);     % channel number (char)
        channelName = sprintf('%d (BAD)',channel_idx);     % channel number (char)
    end
    channelName_cell{channel_idx} = channelName;
    amplitude_arr(channel_idx) = amplitude;
    chargePhase_arr(channel_idx) = chargePhase;
    chargeInjection_arr(channel_idx) = chargeInjection;
    potentialExcursion_arr(channel_idx) = potentialExcursion(1);
    accessVoltage_arr(channel_idx) = accessVoltage(1);
    drivingVoltage_arr(channel_idx) = drivingVoltage(1);
    accessResistance_arr(channel_idx) = accessResistance(1);

    % Table
    if isMultiTest
        if hasExternal
            tableHeading = { ...     % heading
                'Time (us)', ...     % time
                'Voltage (V)', ...   % voltage transient
                'Working (V)', ...
                'Counter (V)', ...
                'Current (uA)', ...  % current stimulation
                'Current Density (A/cm2)', ...
                'Amplitude (uA)', ...
                'Qph (nC/ph)', ...   % charge per phase
                'Area (um2)', ...     % geometric surface area
                'Qinj (mC/cm2)', ... % charge injection
                potentialExcursion_label, ...       % potential excursion
                'Vacc (V)', ...
                'Vdrive (V)', ...    % driving voltage
                'Racc (kOhm)', ...
                'Date Time', ...% date and time
                'Status'};
            tableData = table( ...               % table
                time, ...                   % time
                voltage, ...                % voltage transient
                working, ...
                counter, ...
                current, ...                % current stimulation
                currentDensity, ...
                amplitude_cell, ...
                chargePhase_cell, ...         % Qph
                surfaceArea_cell, ...     % GSA
                chargeInjection_cell, ...           % Qinj
                potentialExcursion_cell, ...        % Emc
                accessVoltage_cell, ...
                drivingVoltage_cell, ...        % Vdrive
                accessResistance_cell, ...
                dateTime_cell, ...            % DateTime
                status_cell, ...
                'VariableNames',tableHeading);
        else
            tableHeading = { ...     % heading
                'Time (us)', ...     % time
                'Voltage (V)', ...   % voltage transient
                'Working (V)', ...
                'Current (uA)', ...  % current stimulation
                'Current Density (A/cm2)', ...
                'Amplitude (uA)', ...
                'Qph (nC/ph)', ...   % charge per phase
                'Area (um2)', ...     % geometric surface area
                'Qinj (mC/cm2)', ... % charge injection
                potentialExcursion_label, ...       % potential excursion
                'Vacc (V)', ...
                'Vdrive (V)', ...    % driving voltage
                'Racc (kOhm)', ...
                'Date Time', ...% date and time
                'Status'};
            tableData = table( ...               % table
                time, ...                   % time
                voltage, ...                % voltage transient
                working, ...
                current, ...                % current stimulation
                currentDensity, ...
                amplitude_cell, ...
                chargePhase_cell, ...         % Qph
                surfaceArea_cell, ...     % GSA
                chargeInjection_cell, ...           % Qinj
                potentialExcursion_cell, ...        % Emc
                accessVoltage_cell, ...
                drivingVoltage_cell, ...        % Vdrive
                accessResistance_cell, ...
                dateTime_cell, ...            % DateTime
                status_cell, ...
                'VariableNames',tableHeading);
        end
    else
        tableHeading = { ...     % heading
            'Time (us)', ...     % time
            'Voltage (V)', ...   % voltage transient
            'Current (uA)', ...  % current stimulation
            'Current Density (A/cm2)', ...
            'Amplitude (uA)', ...
            'Qph (nC/ph)', ...   % charge per phase
            'Area (um2)', ...     % geometric surface area
            'Qinj (mC/cm2)', ... % charge injection
            potentialExcursion_label, ...       % potential excursion
            'Vacc (V)', ...
            'Vdrive (V)', ...    % driving voltage
            'Racc (kOhm)', ...
            'Date Time', ...% date and time
            'Status'};
        tableData = table( ...               % table
            time, ...                   % time
            voltage, ...                % voltage transient
            current, ...                % current stimulation
            currentDensity, ...
            amplitude_cell, ...
            chargePhase_cell, ...         % Qph
            surfaceArea_cell, ...     % GSA
            chargeInjection_cell, ...           % Qinj
            potentialExcursion_cell, ...        % Emc
            accessVoltage_cell, ...
            drivingVoltage_cell, ...        % Vdrive
            accessResistance_cell, ...
            dateTime_cell, ...            % DateTime
            status_cell, ...
            'VariableNames',tableHeading);
    end
    % Save sheet
    if channel_idx == channelNum
        writetable(tableData,filepath_xlsx,'Sheet',channelSheet);    % save sheet to .xlsx file
        fprintf('Channel %d...',channel_idx);
    end
end

% Compiled Value Sheet
data_arr = [...
    amplitude_arr;...
    chargePhase_arr;...
    chargeInjection_arr;...
    potentialExcursion_arr;...
    accessVoltage_arr;...
    drivingVoltage_arr;...
    accessResistance_arr];
rowHeadings = { ...     % heading
    'Amplitude (uA)', ...
    'Qph (nC/ph)', ...   % charge per phase
    'Qinj (mC/cm2)', ... % charge injection
    potentialExcursion_label, ...       % potential excursion
    'Vacc (V)', ...
    'Vdrive (V)', ...   % driving voltage
    'Racc (kOhm)'}; 
dataTable = array2table( ...
    data_arr, ...
    'VariableNames',channelName_cell, ...
    'RowNames',rowHeadings);
writetable(dataTable,filepath_xlsx,'Sheet','Values','WriteRowNames',true);    % save sheet to .xlsx file
writetable(dataTable,filepath_xlsx,'Sheet','Values','WriteRowNames',true);    % save sheet to .xlsx file
[endTime,unit] = getEndTime(startTime);
fprintf('"%s" (%.2f %s)\n',filename_xlsx,endTime,unit);	% .xlsx file saved
data_arr_round = [...
    round(amplitude_arr,2);...
    round(chargePhase_arr,2);...
    round(chargeInjection_arr,3);...
    round(potentialExcursion_arr,3);...
    round(accessVoltage_arr,3,'significant');...
    round(drivingVoltage_arr,3,'significant');...
    round(accessResistance_arr,1)];
dataTable_round = array2table( ...
    data_arr_round, ...
    'VariableNames',channelName_cell, ...
    'RowNames',rowHeadings);
disp(dataTable_round);

end