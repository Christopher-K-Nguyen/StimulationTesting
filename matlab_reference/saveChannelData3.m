function filepath_tif = saveChannelData3(...
    File,...
    channelNum,...
    isBroken,...
    filename,...
    savePath)
%% Variables
% Channel Data
Data = File.Data(channelNum);
time_arr = Data.Time;              % time data array
voltage_arr = Data.Voltage;            % voltage data array
current_arr = Data.Current;          % current data array
amplitude = Data.Amplitude;      	% amplitude value
chargePhase = Data.ChargePhase;      	% charge/phase value
surfaceArea = Data.SurfaceArea;% geometric surface area
chargeInjection = Data.ChargeInjection;           % charge injection value
potentialExcursion_arr = Data.PotentialExcursion;   % maximum cathodal potential value
accessVoltage_arr = Data.AccessVoltage;         % access voltage
drivingVoltage_arr = Data.DrivingVoltage;           % driving voltage value
dateTime = Data.DateTime;                   % date and time completed
status = Data.Status.Description;

% Initialize Blank Array
data_len = length(time_arr);
cell_arr = cell(data_len,1);	% array of blank space

% Initialize Arrays
amplitude_cell = cell_arr;
chargePhase_cell = cell_arr;
surfaceArea_cell = cell_arr;
chargeInjection_cell = cell_arr;
potentialExcursion_cell = cell_arr;
accessVoltage_cell = cell_arr;
drivingVoltage_cell = cell_arr;
dateTime_cell = cell_arr;
status_cell = cell_arr;

%% File Names
% Starting filename
filename_char = sprintf('%s_CH%02d',... % charge per phase
filename,channelNum);   
if isBroken
    filename_char = append(filename_char,'_BAD');
end

% .mat filepath
filename_mat = sprintf('%s.mat',filename_char);  % filename for .mat file (char)
filepath_mat = fullfile(savePath,filename_mat);% save path for .mat file

% .csv filepath
filename_csv = sprintf('%s.csv',filename_char);  % filename for .csv file (char)
filepath_csv = fullfile(savePath,filename_csv);                    % save path for .csv file

%% Save
% Single Values
amplitude_cell{1} = amplitude;
chargePhase_cell{1} = chargePhase; 
surfaceArea_cell{1} = surfaceArea;
chargeInjection_cell{1} = chargeInjection;
dateTime_cell{1} = dateTime;
status_cell{1} = status;

% Multiple Values
for idx = 1:2
    potentialExcursion_cell{idx} = potentialExcursion_arr(idx);
    accessVoltage_cell{idx} = accessVoltage_arr(idx);
    drivingVoltage_cell{idx} = drivingVoltage_arr(idx);
end

% Table
if all(potentialExcursion_arr < 0)
    potentialExcursionTitle = 'Emc (V)';
else
    potentialExcursionTitle = 'Ema (V)';
end
tableHeading = {...     % heading
    'Time (us)',...     % time
    'Voltage (V)',...   % voltage transient
    'Current (uA)',...  % current stimulation
    'Amplitude (uA)',...
    'Qph (nC/ph)',...   % charge per phase
    'Area (um2)',...     % geometric surface area
    'Qinj (mC/cm2)',... % charge injection
    potentialExcursionTitle,...       % max potential
    'Vacc (V)',...
    'Vdrive (V)',...    % driving voltage
    'Date Time',...% date and time
    'Status'};
tableData = table(...               % table
    time_arr,...                   % time
    voltage_arr,...                % voltage transient
    current_arr,...                % current stimulation
    amplitude_cell,...
    chargePhase_cell,...         % Qph
    surfaceArea_cell,...     % GSA
    chargeInjection_cell,...           % Qinj
    potentialExcursion_cell,...        % Emc
    accessVoltage_cell,...
    drivingVoltage_cell,...        % Vdrive
    dateTime_cell,...            % DateTime
    status_cell,...
    'VariableNames',tableHeading);

% Making .mat file
fprintf('Saving "%s"...',filename_mat);  
save(filepath_mat,'Data');          % save .mat file
% fprintf('%s\n',filename_mat);                       % .mat file saved
fprintf('OK.\n');  

% Save spreadsheet
fprintf('Saving "%s"...',filename_csv);	% .csv file saved
writetable(tableData,filepath_csv);% save .csv file
fprintf('OK.\n');
    
% Save figure
vtPlot = Data.Figure;
if ishandle(vtPlot)
    filename_tif = sprintf('%s.tif',filename_char);  % filename for .tif file (char)
    filepath_tif = fullfile(savePath,filename_tif);
    fprintf('Saving "%s"...',filename_tif);           % .csv file saved
    print(vtPlot,'-dtiff',filepath_tif,'-r300');
    fprintf('OK.\n');   % .tif file saved
else
    filepath_tif = [];
end

end