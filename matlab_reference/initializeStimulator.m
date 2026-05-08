function [File,isQuit] = initializeStimulator(File)
%% Constants
NIL_LIST = {'PLX00078','PLX00089','PLX00161'};

%% Variables
% Experiment
expType = File.Test.Experiment;
isPulsing = contains2(expType,{'SP','LP'});


% Configuration
channelGroup_mat = File.Test.Groups;
[numOfGroups,~] = size(channelGroup_mat);

% Oscilloscope
numOfDevices = length(File.Oscilloscope);

% Initialization
isQuit = false;
errInitAllStim = 1;
pauseTime = 5;

serial = '';
firmwareVer = '';
description = '';
voltageScaling = [];
currentScaling = [];
dischargeMode = 1;
isDischargeOn = logical(dischargeMode);
isOn = false;
% Offset = struct();
% for deviceNum = 1:numOfDevices
%     scopeChannelSelect_cell = File.Oscilloscope(deviceNum).Channels;
%     numOfScopeChannels = File.Oscilloscope(deviceNum).NumberOfChannels;
%     for scopeChannel_idx = 1:numOfScopeChannels
%         scopeChannel = scopeChannelSelect_cell{scopeChannel_idx};
%         Offset(deviceNum).(scopeChannel) = [];
%     end
% end

%% Store
File.Stimulator.SerialNumber = serial;
File.Stimulator.Firmware = firmwareVer;
File.Stimulator.Description = description;
File.Stimulator.VoltageScaling = voltageScaling;
File.Stimulator.CurrentScaling = currentScaling;
File.Stimulator.Discharge = isDischargeOn;
File.Stimulator.Status = isOn;
% File.Stimulator.Offset = Offset;
% File.Stimulator.Offset(1:numOfGroups) = Offset;

%% Function
try
    % Find stimulators
    fprintf('Initializing stimulator(s)...');	% initializing stimulator(s)
    startTime = tic;
    PS_CloseAllStim;              % already initialized cause errors
    errInitAllStim = PS_InitAllStim();      % get errors

    % if errInitAllStim ~= 0
    %     try
            % fprintf('FAILED\n');
            % % Run the PowerShell command and export to CSV for full output
            % system('powershell "Get-WmiObject Win32_PnPEntity | Select-Object Status, Class, DeviceID, Name | Export-Csv -Path devices.csv -NoTypeInformation"');
            % 
            % % Read the CSV file into MATLAB
            % opts = detectImportOptions('devices.csv','ReadVariableNames',true);  % Auto-detect column headers
            % devicesTable = readtable('devices.csv', opts);
            % 
            % deviceID_cell = devicesTable.DeviceID;
            % plexon_tf = containsi(deviceID_cell,{'plexon','plx'});
            % deviceStatus = char(devicesTable.Status(plexon_tf));
            % deviceName = char(devicesTable.Name(plexon_tf));
            % deviceID = char(devicesTable.DeviceID(plexon_tf));
            % 
            % % Path to the DevCon executable
            % devconPath = '"C:\Program Files (x86)\Windows Kits\10\Tools\10.0.26100.0\x64\devcon.exe"';  % Update this path
            % 
            % % Disable the USB device
            % system([devconPath ' disable "' deviceID '"']);
            % 
            % % Enable the USB device (re-enabling it)
            % system([devconPath ' enable "' deviceID '"']);
            % 
            % % Check device status
            % system([devconPath ' status "' deviceID '"']);
        
    %         fprintf('Resetting USB connections...')
    %         % Disable USB devices
    %         disable_cmd = 'powershell "Get-PnpDevice -Class USB | Disable-PnpDevice -Confirm:$false"';
    %         [status,~] = system(disable_cmd);
    %         if status == 0
    %             fprintf('disabled...');
    %         else
    %             fprintf('FAILED\n');
    %         end
    % 
    %         if status == 0
    %             % Pause to allow the system to apply changes
    %             pause(5); % Pausing for 5 seconds
    % 
    %             % Re-enable USB devices
    %             enable_cmd = 'powershell "Get-PnpDevice -Class USB | Enable-PnpDevice -Confirm:$false"';
    %             [status,~] = system(enable_cmd);
    %             if status == 0
    %                 fprintf('enabled\n');
    %                 fprintf('Initializing stimulator(s)...');	% initializing stimulator(s)
    %                 errInitAllStim = PS_InitAllStim();      % get errors
    %             else
    %                 fprintf('FAILED\n');
    %             end    
    %         end
    %     catch
    %     end
    % end

    % idx = 0;
    % while errInitAllStim ~= 0
    %     idx = idx + 1;
    %     errInitAllStim = PS_InitAllStim();      % get errors
    %     if errInitAllStim ~= 0
    %         PS_CloseAllStim;              % already initialized cause errors
    %         if idx > 1
    %             pause(pauseTime);
    %         end
    %     end
    %     if idx > 100
    %         break;
    %     end
    % end
    if errInitAllStim == 0
        [endTime,unit] = getEndTime(startTime);
        fprintf('OK (%.3f %s)\n',endTime,unit);   % stimulator(s) initialized

        % Serial Number
        fprintf('\tSerial number...');
        startTime = tic;
        [serial,~] = PS_GetSerialNumber(1);
        [endTime,unit] = getEndTime(startTime);
        fprintf('%s (%.3f %s)\n',serial,endTime,unit);

        % Voltage Monitor Scaling
        fprintf('\tVoltage monitor scaling...');
        startTime = tic;
        if contains2(serial,NIL_LIST)
            voltageScaling = 1;     % V/V
        else
            voltageScaling = 0.25;  % V/V
        end
        [endTime,unit] = getEndTime(startTime);
        fprintf('%g V/V (%.3f %s)\n',voltageScaling,endTime,unit);

        % Current Monitor Scaling
        fprintf('\tCurrent monitor scaling...');
        startTime = tic;
        if contains2(serial,NIL_LIST)
            currentScaling = 1e-3;  % V/uA
        else
            currentScaling = 2.5;   % V/uA
        end
        [endTime,unit] = getEndTime(startTime);
        fprintf('%g V/uA (%.3f %s)\n',currentScaling,endTime,unit);

        % Firmware Verions
        fprintf('\tFirmware version...');
        startTime = tic;
        [firmwareVer,~] = PS_GetFwVersion(1);
        [endTime,unit] = getEndTime(startTime);
        fprintf('%g (%.3f %s)\n',firmwareVer,endTime,unit);

        % Description
        fprintf('\tDescription...');
        startTime = tic;
        [description,~] = PS_GetDescription(1);
        [endTime,unit] = getEndTime(startTime);
        fprintf('%s (%.3f %s)\n',description,endTime,unit);
        isOn = true;

        % % Discharge Mode
        % fprintf('\tDischarge mode...');
        % PS_SetAutoDischarge(1,dischargeMode);
        % [dischargeMode_check,~] = PS_GetAutoDischarge(1);
        % switch dischargeMode_check
        %     case 1
        %         dischargeMode_status = 'ON';
        %     case 0
        %         dischargeMode_status = 'OFF';
        % end
        % [endTime,unit] = getEndTime(startTime);
        % fprintf('%s (%.3f %s)\n',dischargeMode_status,endTime,unit);

    else
        fprintf('NO STIMULATORS CONNECTED!\n');
        isOn = false;
        isQuit = true;
        fprintf('\nQuitting...');	% quitting
        return;
    end

    % Check stimulators
    [numOfStim,isQuit] = getNumOfStim();
    if isQuit
        fprintf('\nQuitting...')
        return;
    end
    if numOfStim == 1
        % Find channels available per stimulators (should be 16, value not used)
        fprintf('\t');
        [~,isQuit] = getChannelsFromStim(numOfStim);
    else
        fprintf('NO STIMULATORS CONNECTED!\n\n');
        isQuit = true;
        fprintf('\nQuitting...');	% quitting
        return;
    end

catch
    isOn = false;
end

%% Store
File.Stimulator.SerialNumber = serial;
File.Stimulator.Firmware = firmwareVer;
File.Stimulator.Description = description;
File.Stimulator.DigitalDelay = 1;
File.Stimulator.VoltageScaling = voltageScaling;
File.Stimulator.CurrentScaling = currentScaling;
File.Stimulator.Discharge = isDischargeOn;
File.Stimulator.Status = isOn;
% Offset = struct();
% for deviceNum = 1:numOfDevices
%     scopeChannelSelect_cell = File.Oscilloscope(deviceNum).Channels;
%     numOfScopeChannels = File.Oscilloscope(deviceNum).NumberOfChannels;
%     for scopeChannel_idx = 1:numOfScopeChannels
%         scopeChannel = scopeChannelSelect_cell{scopeChannel_idx};
%         Offset(deviceNum).(scopeChannel) = [];
%     end
% end
% File.Stimulator.Offset = Offset;
% File.Stimulator.Offset(1:numOfGroups) = Offset;


end